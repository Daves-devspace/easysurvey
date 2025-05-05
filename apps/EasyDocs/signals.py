from decimal import Decimal
import logging

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils import timezone

from apps.EasyDocs.communication import send_and_log_sms
from apps.EasyDocs.models import (
    ClientServiceProcess, TitleDeedCollection, ClientService,
    Process, Payment, PaymentHistory, ServiceCategory, ClientSubService
)

logger = logging.getLogger(__name__)


def send_process_sms(client_service, client, phone, message, reason):
    """Send SMS and log it if phone and message are present."""
    if phone and message:
        log = send_and_log_sms(
            client_service=client_service,
            client=client,
            phone=phone,
            message=message,
            reason=reason
        )
        logger.info(f"SMS log #{log.id}: send_status={log.send_status}")
        return log
    return None  # Explicit return if conditions not met



@receiver(post_save, sender=ClientService)
def client_service_created_handler(sender, instance, created, **kwargs):
    if not created:
        return

    service, client = instance.service, instance.client

    if service.category == ServiceCategory.TITLE and not instance.service_processes.exists():
        processes = Process.objects.filter(service=service).order_by('step_order')
        for i, process in enumerate(processes):
            status = 'in_progress' if i == 0 else 'pending'
            ClientServiceProcess.objects.create(
                client_service=instance,
                process=process,
                status=status
            )
            if i == 0:
                reason = f"{service.name} – process: {process.name}"
                send_process_sms(instance, client, client.phone, process.message, reason)

    elif service.category == ServiceCategory.GROUND:
        if getattr(service, "dispatch_message", None):
            reason = f"{service.name} – dispatch"
            send_process_sms(instance, client, client.phone, service.dispatch_message, reason)




def update_full_total(client_service: ClientService):
    """
    Recalculate and persist the full_total_price for a given ClientService.
    """
    try:
        client_service.full_total_price = client_service._calculate_full_total()
        client_service.save(update_fields=['full_total_price'])
        logger.info(f"Recalculated full_total_price for ClientService #{client_service.pk}: {client_service.full_total_price}")
    except Exception as e:
        logger.error(f"Failed to update full_total_price for ClientService #{client_service.pk}: {e}", exc_info=True)


@receiver(post_save, sender=ClientSubService)
@receiver(post_delete, sender=ClientSubService)
def on_subservice_change(sender, instance, **kwargs):
    """
    When a ClientSubService is added, updated, or removed,
    update the parent ClientService's total price.
    """
    update_full_total(instance.client_service)


@receiver(post_save, sender=ClientServiceProcess)
@receiver(post_delete, sender=ClientServiceProcess)
def on_process_change(sender, instance, **kwargs):
    """
    When a ClientServiceProcess is added, updated, or removed,
    update the parent ClientService's total price.
    """
    update_full_total(instance.client_service)



@receiver(post_save, sender=TitleDeedCollection)
def title_deed_collected_handler(sender, instance, created, **kwargs):
    if not created:
        return

    svc = instance.client_service
    last_process = svc.service_processes.order_by('-process__step_order').first()

    if last_process and last_process.status in ['completed', 'pending']:
        last_process.status = 'collected'
        last_process.completed_at = instance.collected_at
        last_process.save(update_fields=['status', 'completed_at'])

    svc.status = 'collected'
    svc.save(update_fields=['status'])

    msg = instance.message or f"Your title deed has been collected by {instance.collected_by}"
    if instance.id_number:
        msg += f" (ID: {instance.id_number})"

    reason = f"{svc.service.name} – deed collected"
    send_process_sms(svc, svc.client, svc.client.phone, msg, reason)


@receiver(post_save, sender=Payment)
def allocate_payment(sender, instance, created, **kwargs):
    if not created:
        return

    remaining = Decimal(str(instance.amount))

    # Allocate to service processes
    for csp in instance.client_service.service_processes.order_by('process__step_order'):
        if remaining <= 0:
            break
        to_pay = min(remaining, csp.pending_amount)
        if to_pay > 0:
            csp.paid_amount += to_pay
            csp.save(update_fields=['paid_amount'])
            remaining -= to_pay
            PaymentHistory.objects.create(
                payment=instance,
                client_service=instance.client_service,
                amount=to_pay,
                reason="service_step",
                service_process=csp
            )

    # Allocate to sub-services
    for sub in instance.client_service.sub_services.all():
        if remaining <= 0:
            break
        to_pay = min(remaining, sub.balance)
        if to_pay > 0:
            sub.paid_amount += to_pay
            sub.save(update_fields=['paid_amount'])
            remaining -= to_pay
            PaymentHistory.objects.create(
                payment=instance,
                client_service=instance.client_service,
                amount=to_pay,
                reason="sub_service",
                sub_service=sub
            )

    # Optional: Handle any remaining balance here if needed
