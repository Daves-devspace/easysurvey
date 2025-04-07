from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
import logging

from apps.EasyDocs.models import ClientServiceProcess, TitleDeedCollection, ClientService, Process
from apps.EasyDocs.utils import MobileSasaAPI

logger = logging.getLogger(__name__)


@receiver(post_save, sender=ClientService)
def client_service_created_handler(sender, instance, created, **kwargs):
    """
    When a new ClientService is created, populate its process steps.
    The first process is set to 'in_progress'.
    """
    print("📣 Signal fired for client service:", instance.id)

    if created:
        service = instance.service
        client = instance.client

        # Avoid duplicates
        if instance.service_processes.exists():
            print("⚠️ Processes already exist for this client service. Skipping.")
            return

        # Fetch ordered processes
        processes = Process.objects.filter(service=service).order_by('step_order')

        for i, process in enumerate(processes):
            status = 'in_progress' if i == 0 else 'pending'

            ClientServiceProcess.objects.create(
                client_service=instance,
                process=process,
                status=status
            )

            # Debug print
            print(f"✅ Created process '{process.name}' with status: {status}")

            if i == 0 and process.message and client.phone:
                try:
                    sms_api = MobileSasaAPI()
                    sms_api.send_sms(client.phone, process.message)
                    print(f"📤 SMS sent to {client.phone}")
                except Exception as e:
                    print(f"❌ Failed to send SMS: {e}")


@receiver(post_save, sender=ClientServiceProcess)
def process_status_handler(sender, instance, **kwargs):
    processes = instance.client_service.service_processes.order_by('process__step_order')
    completed_processes = processes.filter(status='completed')
    last_completed_step = completed_processes.last().process.step_order if completed_processes.exists() else 0

    for process_instance in processes:
        if process_instance.process.step_order == last_completed_step + 1 and process_instance.status != 'in_progress':
            new_status = 'pending' if process_instance == processes.last() else 'in_progress'
            ClientServiceProcess.objects.filter(pk=process_instance.pk).update(status=new_status)

            if new_status == 'in_progress':
                client_phone = instance.client_service.client.phone
                message = process_instance.process.message
                if client_phone and message:
                    try:
                        sms_api = MobileSasaAPI()
                        sms_api.send_sms(client_phone, message)
                    except Exception as e:
                        logger.warning(f"Failed to send SMS to {client_phone}: {e}")

        elif process_instance.process.step_order <= last_completed_step and process_instance.status != 'completed':
            ClientServiceProcess.objects.filter(pk=process_instance.pk).update(
                status='completed',
                completed_at=timezone.now()
            )

    # Update the client service status
    new_service_status = 'completed' if all(p.status in ['completed', 'pending'] for p in processes) else 'active'
    instance.client_service.__class__.objects.filter(pk=instance.client_service.pk).update(status=new_service_status)

    # Send SMS if the last process is pending
    last_process = processes.last()
    if last_process.status == 'pending':
        client_phone = instance.client_service.client.phone
        message = last_process.process.message
        if client_phone and message:
            try:
                sms_api = MobileSasaAPI()
                sms_api.send_sms(client_phone, message)
            except Exception as e:
                logger.warning(f"Failed to send SMS to {client_phone}: {e}")

@receiver(post_save, sender=TitleDeedCollection)
def title_deed_collected_handler(sender, instance, created, **kwargs):
    if created:
        client_service = instance.client_service
        last_process = client_service.service_processes.order_by('-process__step_order').first()
        if last_process and last_process.status in ['completed', 'pending']:
            last_process.status = 'collected'
            last_process.completed_at = timezone.now()
            last_process.save()

            client_phone = client_service.client.phone
            message = f"Your title deed has been collected by {instance.collected_by}"
            if instance.id_number:
                message += f" (ID: {instance.id_number})"
            try:
                sms_api = MobileSasaAPI()
                sms_api.send_sms(client_phone, message)
            except Exception as e:
                logger.warning(f"Failed to send collection SMS to {client_phone}: {e}")

            # 🟢 UPDATE ClientService status
            client_service.status = 'collected'
            client_service.save()