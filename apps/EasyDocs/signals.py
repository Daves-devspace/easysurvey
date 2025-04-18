from decimal import Decimal

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils import timezone
import logging

from apps.EasyDocs.models import ClientServiceProcess, TitleDeedCollection, ClientService, Process, Payment, \
    PaymentHistory, ServiceCategory

from apps.EasyDocs.utils import MobileSasaAPI

logger = logging.getLogger(__name__)


@receiver(post_save, sender=ClientService)
def client_service_created_handler(sender, instance, created, **kwargs):
    """
    When a new ClientService is created, handle either:
    - Populating process steps (if service is process-based)
    - Sending dispatch SMS (if service is dispatch-based)
    """
    print("📣 Signal fired for client service:", instance.id)

    if not created:
        return

    service = instance.service
    client = instance.client

    if service.category == ServiceCategory.TITLE:
        # Avoid duplicates
        if instance.service_processes.exists():
            print("⚠️ Processes already exist for this client service. Skipping.")
            return

        # Create process steps
        processes = Process.objects.filter(service=service).order_by('step_order')
        for i, process in enumerate(processes):
            status = 'in_progress' if i == 0 else 'pending'

            ClientServiceProcess.objects.create(
                client_service=instance,
                process=process,
                status=status
            )

            print(f"✅ Created process '{process.name}' with status: {status}")

            # Send SMS for first process
            if i == 0 and process.message and client.phone:
                try:
                    sms_api = MobileSasaAPI()
                    sms_api.send_sms(client.phone, process.message)
                    print(f"📤 SMS sent to {client.phone}")
                except Exception as e:
                    print(f"❌ Failed to send SMS: {e}")

    elif service.category == ServiceCategory.GROUND:
        # Dispatch-based services don't have processes
        # Send a predefined dispatch message
        dispatch_message = getattr(service, "dispatch_message", None)  # optional custom field
        if dispatch_message and client.phone:
            try:
                sms_api = MobileSasaAPI()
                sms_api.send_sms(client.phone, dispatch_message)
                print(f"📤 Dispatch SMS sent to {client.phone}")
            except Exception as e:
                print(f"❌ Failed to send Dispatch SMS: {e}")




@receiver(post_save, sender=ClientServiceProcess)
def process_status_handler(sender, instance, **kwargs):
    # If this process was just collected, skip all logic
    if instance.status == 'collected':
        return

    processes = instance.client_service.service_processes.order_by('process__step_order')
    completed = processes.filter(status='completed')
    last_completed_step = completed.last().process.step_order if completed.exists() else 0
    last_step = processes.last()

    # Track if we just completed the final step
    just_completed_last = False

    for step in processes:
        # 1) Mark any earlier steps as completed
        if step.process.step_order <= last_completed_step and step.status != 'completed':
            ClientServiceProcess.objects.filter(pk=step.pk).update(
                status='completed',
                completed_at=timezone.now()
            )
            if step == last_step:
                just_completed_last = True

        # 2) Kick off the next step
        elif step.process.step_order == last_completed_step + 1 and step.status == 'pending':
            # final step stays pending until confirmed; others go in_progress
            new_status = 'pending' if step == last_step else 'in_progress'
            ClientServiceProcess.objects.filter(pk=step.pk).update(status=new_status)

            # Send SMS only for non-final in_progress steps
            if new_status == 'in_progress':
                client_phone = instance.client_service.client.phone
                message = step.process.message
                if client_phone and message:
                    try:
                        sms_api = MobileSasaAPI()
                        sms_api.send_sms(client_phone, message)
                    except Exception as e:
                        logger.warning(f"Failed to send SMS to {client_phone}: {e}")

    # 3) Update the overall ClientService status
    all_done = all(p.status in ['completed', 'pending'] for p in processes)
    instance.client_service.__class__.objects.filter(
        pk=instance.client_service.pk
    ).update(status='completed' if all_done else 'active')

    # 4) If we just completed the final step, send its SMS
    if just_completed_last and not instance.client_service.title_deed_collection:
        client_phone = instance.client_service.client.phone
        message = last_step.process.message
        if client_phone and message:
            try:
                sms_api = MobileSasaAPI()
                sms_api.send_sms(client_phone, message)
            except Exception as e:
                logger.warning(f"Failed to send final-step SMS to {client_phone}: {e}")


@receiver(post_save, sender=TitleDeedCollection)
def title_deed_collected_handler(sender, instance, created, **kwargs):
    if not created:
        return

    client_service = instance.client_service
    last_process = client_service.service_processes.order_by('-process__step_order').first()

    if last_process and last_process.status in ['completed', 'pending']:
        last_process.status = 'collected'
        # **bump completed_at to now so ordering picks it**
        last_process.completed_at = instance.collected_at
        last_process.save()

    # Update the overall ClientService status
    client_service.status = 'collected'
    client_service.save()

    # Get the custom message from the TitleDeedCollection model
    message = instance.message if instance.message else f"Your title deed has been collected by {instance.collected_by}"
    if instance.id_number:
        message += f" (ID: {instance.id_number})"

    # Send the SMS notification to the client
    client_phone = client_service.client.phone
    try:
        sms_api = MobileSasaAPI()
        sms_api.send_sms(client_phone, message)
    except Exception as e:
        logger.warning(f"Failed to send collection SMS to {client_phone}: {e}")



@receiver(post_save, sender=Payment)
def allocate_payment(sender, instance, created, **kwargs):
    if not created:
        return

    remaining = Decimal(str(instance.amount))

    # Allocate to service processes first
    for csp in instance.client_service.service_processes.order_by('process__step_order'):
        if remaining <= 0:
            break
        to_pay = min(remaining, csp.pending_amount)  # Pay the minimum of the remaining amount or the pending amount
        if to_pay > 0:
            csp.paid_amount += to_pay  # Update the paid amount for the process step
            csp.save(update_fields=['paid_amount'])  # Save the updated service process
            remaining -= to_pay  # Subtract the paid amount from the remaining balance
            PaymentHistory.objects.create(
                payment=instance,
                client_service=instance.client_service,
                amount=to_pay,
                reason="service_step",  # Reason for payment: allocated to a service process step
                service_process=csp  # Link this payment to the service process
            )

    # Allocate remaining balance to sub-services if any
    for css in instance.client_service.sub_services.all():
        if remaining <= 0:
            break
        to_pay = min(remaining, css.balance)  # Pay the minimum of remaining amount or sub-service balance
        if to_pay > 0:
            css.paid_amount += to_pay  # Update the paid amount for the sub-service
            css.save(update_fields=['paid_amount'])  # Save the updated sub-service
            remaining -= to_pay  # Subtract the paid amount from the remaining balance
            PaymentHistory.objects.create(
                payment=instance,
                client_service=instance.client_service,
                amount=to_pay,
                reason="sub_service",  # Reason for payment: allocated to a sub-service
                sub_service=css  # Link this payment to the sub-service
            )

    # Optional: Handle any remaining balance, e.g., if there is unallocated money
    if remaining > 0:
        # You can handle remaining unallocated balance here, or log it as necessary.
        pass


