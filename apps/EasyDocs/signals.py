from decimal import Decimal

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils import timezone
import logging

from apps.EasyDocs.communication import send_and_log_sms
from apps.EasyDocs.models import ClientServiceProcess, TitleDeedCollection, ClientService, Process, Payment, \
    PaymentHistory, ServiceCategory

from apps.EasyDocs.utils import MobileSasaAPI

logger = logging.getLogger(__name__)


@receiver(post_save, sender=ClientService)
def client_service_created_handler(sender, instance, created, **kwargs):
    if not created:
        return

    service = instance.service
    client  = instance.client

    if service.category == ServiceCategory.TITLE:
        if instance.service_processes.exists():
            return

        processes = Process.objects.filter(service=service).order_by('step_order')
        for i, process in enumerate(processes):
            status = 'in_progress' if i == 0 else 'pending'
            ClientServiceProcess.objects.create(
                client_service=instance,
                process=process,
                status=status
            )

            # only send/log for the first step
            if i == 0 and process.message and client.phone:
                reason = f"{service.name} – process: {process.name}"
                log = send_and_log_sms(
                    client_service=instance,
                    client=client,
                    phone=client.phone,
                    message=process.message,
                    reason=reason
                )
                print(f"SMS log #{log.id} created: send_status={log.send_status}")

    elif service.category == ServiceCategory.GROUND:
        dispatch_message = getattr(service, "dispatch_message", None)
        if dispatch_message and client.phone:
            reason = f"{service.name} – dispatch"
            log = send_and_log_sms(
                client_service=instance,
                client=client,
                phone=client.phone,
                message=dispatch_message,
                reason=reason
            )
            print(f"Dispatch SMS log #{log.id}: send_status={log.send_status}")




@receiver(post_save, sender=ClientServiceProcess)
def process_status_handler(sender, instance, **kwargs):
    if instance.status == 'collected':
        return

    svc     = instance.client_service
    processes = svc.service_processes.order_by('process__step_order')
    completed = processes.filter(status='completed')
    last_completed = completed.last().process.step_order if completed.exists() else 0
    last_step      = processes.last()
    just_completed_last = False

    for step in processes:
        # mark earlier steps complete
        if step.process.step_order <= last_completed and step.status != 'completed':
            ClientServiceProcess.objects.filter(pk=step.pk).update(
                status='completed',
                completed_at=timezone.now()
            )
            if step == last_step:
                just_completed_last = True

        # advance to next
        elif step.process.step_order == last_completed + 1 and step.status == 'pending':
            new_status = 'pending' if step == last_step else 'in_progress'
            ClientServiceProcess.objects.filter(pk=step.pk).update(status=new_status)

            if new_status == 'in_progress' and svc.client.phone and step.process.message:
                reason = f"{svc.service.name} – process: {step.process.name}"
                log = send_and_log_sms(
                    client_service=svc,
                    client=svc.client,
                    phone=svc.client.phone,
                    message=step.process.message,
                    reason=reason
                )
                logger.info(f"SMS log #{log.id} for in-progress step: {log.send_status}")

    # update overall status
    all_done = all(p.status in ['completed', 'pending'] for p in processes)
    svc.__class__.objects.filter(pk=svc.pk).update(
        status='completed' if all_done else 'active'
    )

    # final-step notification
    if just_completed_last and not svc.title_deed_collection:
        last_msg = last_step.process.message
        if svc.client.phone and last_msg:
            reason = f"{svc.service.name} – final process: {last_step.process.name}"
            log = send_and_log_sms(
                client_service=svc,
                client=svc.client,
                phone=svc.client.phone,
                message=last_msg,
                reason=reason
            )
            logger.info(f"SMS log #{log.id} for final step: {log.send_status}")


@receiver(post_save, sender=TitleDeedCollection)
def title_deed_collected_handler(sender, instance, created, **kwargs):
    if not created:
        return

    svc = instance.client_service
    last_process = svc.service_processes.order_by('-process__step_order').first()

    if last_process and last_process.status in ['completed', 'pending']:
        last_process.status = 'collected'
        last_process.completed_at = instance.collected_at
        last_process.save()

    svc.status = 'collected'
    svc.save()

    # build the SMS
    msg = instance.message or f"Your title deed has been collected by {instance.collected_by}"
    if instance.id_number:
        msg += f" (ID: {instance.id_number})"

    # log the send
    if svc.client.phone:
        reason = f"{svc.service.name} – deed collected"
        log = send_and_log_sms(
            client_service=svc,
            client=svc.client,
            phone=svc.client.phone,
            message=msg,
            reason=reason
        )
        logger.info(f"SMS log #{log.id} for collection notice: {log.send_status}")



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


