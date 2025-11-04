# core/tasks.py

from datetime import datetime

from celery import shared_task, group
from django.db import transaction
from django.utils import timezone

from apps.EasyDocs.models import Client, MessageLog, Booking, ScheduledTask
from apps.EasyDocs.utils import update_pending_sms_logs_and_balance,MobileSasaAPI, personalize

from django.core.mail import send_mail
from django.utils import timezone
from datetime import datetime
from django.conf import settings
from django.template.loader import render_to_string
from django.core.mail import EmailMultiAlternatives
from datetime import datetime, timedelta
import time
from .files.tasks import migrate_documents_to_drive_task

__all__ = ["migrate_documents_to_drive_task"]

# at the top of apps/EasyDocs/tasks.py
import logging
logger = logging.getLogger(__name__)







@shared_task
def update_sms_delivery_and_balance():
    print("Running scheduled SMS delivery and balance update")
    update_pending_sms_logs_and_balance()


# tasks.py
@shared_task
def test_timezone_task():
    print("[CELERY] timezone.now():", timezone.now())  # Django-aware time
    print("[CELERY] datetime.now():", datetime.now())  # Naive system time
    print("[CELERY] datetime.utcnow():", datetime.utcnow())  # Naive UTC time
    print("[CELERY] timezone.get_current_timezone():", timezone.get_current_timezone())  # Django timezone
    print("[CELERY] System timezone:", time.tzname)  # OS-level timezone



BATCH_SIZE = 50



@shared_task
def retry_failed_sms(log_id=None):
    """
    Safely retry a failed sms by log id.
    If called without a log_id (e.g. scheduled call), this becomes a no-op.
    """
    if log_id is None:
        logger.info("retry_failed_sms called without log_id — skipping.")
        return

    from .utils import send_single_sms

    try:
        log = MessageLog.objects.get(id=log_id)
    except MessageLog.DoesNotExist:
        logger.warning("retry_failed_sms: MessageLog id=%s does not exist.", log_id)
        return

    client = log.client

    try:
        status, response = send_single_sms(client, log.message)  # utils.send_single_sms(client, text)
        log.send_status = 'success' if status == 'sent' else 'failed'
        log.message_id = response.get('message_id', '') if isinstance(response, dict) else ''
        log.error_details = None if status == 'sent' else (response.get('message') if isinstance(response, dict) else str(response))
    except Exception as e:
        log.send_status = 'failed'
        log.error_details = str(e)

    log.save()




@shared_task(bind=True)
def _send_chunk(self, template, client_ids):
    message_pairs = []
    logs_map = {}  # phone -> MessageLog object

    for cid in client_ids:
        try:
            client = Client.objects.get(pk=cid)
            message = personalize(template, client)

            cleaned_phone = client.phone  # Or MobileSasaAPI.clean_phone_number(client.phone) if needed

            message_pairs.append({'phone': cleaned_phone, 'message': message})

            log_entry = MessageLog.objects.create(
                client=client,
                phone=cleaned_phone,
                message=message,
                reason='Bulk SMS broadcast',
                send_status='pending',
                delivery_status='pending',
            )
            logs_map[cleaned_phone] = log_entry

        except Exception as e:
            logger.error(f"Failed to create MessageLog for client {cid}: {e}")

    if not message_pairs:
        return {'status': 'no messages to send', 'processed_clients': 0}

    try:
        api = MobileSasaAPI()
        result = api.send_personalized_sms(message_pairs)

        # Update logs based on phone numbers in result
        for phone, log in logs_map.items():
            if phone in result.get('sent', []):
                log.send_status = 'sent'
                log.delivery_status = 'pending'
            elif phone in result.get('failed', []):
                log.send_status = 'failed'
                log.delivery_status = 'failed'
            else:
                # If phone is missing from both lists, mark as unknown or failed
                log.send_status = 'failed'
                log.delivery_status = 'failed'
            log.save()

    except Exception as e:
        logger.error(f"Failed to send personalized SMS: {e}")
        # Mark all as failed in case of exception
        for log in logs_map.values():
            log.send_status = 'failed'
            log.delivery_status = 'failed'
            log.error_details = str(e)
            log.save()

    return {'status': 'completed', 'processed_clients': len(client_ids)}



@shared_task(bind=True)
def schedule_bulk_broadcast(self, template=None, scheduled_iso=None):
    """
    Safely schedule or dispatch bulk broadcast chunks.
    If template is falsy (None/empty), task will no-op to avoid scheduler errors.
    """
    logger.info(f"[SCHEDULER START] schedule_bulk_broadcast triggered. Task ID: {getattr(self.request, 'id', None)}")

    if not template:
        logger.warning("[SCHEDULER] no template provided - nothing to do. Exiting.")
        return {'task_ids': [], 'chunks': 0}

    client_ids = list(Client.objects.values_list('id', flat=True))
    logger.info(f"[INFO] Found {len(client_ids)} clients to broadcast to.")

    chunks = [client_ids[i: i + BATCH_SIZE] for i in range(0, len(client_ids), BATCH_SIZE)]
    logger.info(f"[INFO] Split clients into {len(chunks)} chunks of max {BATCH_SIZE} clients each.")

    scheduled_ids = []

    for i, chunk in enumerate(chunks):
        if scheduled_iso:
            try:
                eta = datetime.fromisoformat(scheduled_iso)
                if timezone.is_naive(eta):
                    eta = timezone.make_aware(eta, timezone.get_current_timezone())
            except Exception as e:
                logger.exception("Invalid scheduled_iso provided: %s", scheduled_iso)
                # fallback: dispatch immediately
                result = _send_chunk.apply_async(args=[template, chunk])
                status = 'sent'
                scheduled_time = timezone.now()
                logger.info(f"[CHUNK {i + 1}] Dispatched immediately (invalid iso) with task ID: {result.id}")
            else:
                result = _send_chunk.apply_async(args=[template, chunk], eta=eta)
                status = 'pending'
                scheduled_time = eta
                logger.info(f"[CHUNK {i + 1}] Scheduled to run at {eta} with task ID: {result.id}")
        else:
            result = _send_chunk.apply_async(args=[template, chunk])
            status = 'sent'
            scheduled_time = timezone.now()
            logger.info(f"[CHUNK {i + 1}] Dispatched immediately with task ID: {result.id}")

        try:
            ScheduledTask.objects.create(
                task_id=result.id,
                task_name='_send_chunk',
                scheduled_time=scheduled_time,
                message_preview=(template[:100] if template else ''),
                status=status
            )
        except Exception as e:
            logger.exception("Failed to create ScheduledTask DB entry for task %s: %s", getattr(result, 'id', None), e)

        scheduled_ids.append(result.id)

    logger.info(f"[SCHEDULER END] Successfully queued {len(scheduled_ids)} tasks.")

    return {'task_ids': scheduled_ids, 'chunks': len(chunks)}




@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_single_sms(self, client_id, text):
    from .utils import send_single_sms as _send_single
    from .models import MessageLog, Client

    client = Client.objects.get(pk=client_id)
    logger.debug(f"→ send_single_sms task starting for client {client_id}")

    try:
        status, raw = _send_single(client, text, reason="Bulk SMS")
        logger.debug(f"   util.send_single_sms returned status={status}")

        log = MessageLog.objects.create(
            client=client,
            phone=client.phone,
            message=text,
            reason="Bulk SMS",
            message_id=raw.get("message_id") if raw else None,
            send_status="sent" if status else "failed",
            delivery_status="pending",
            error_details=None if status else "Unknown sending failure"
        )
        logger.info(f"✅ Created MessageLog id={log.id} for client {client_id}")

        return status

    except Exception as exc:
        logger.error(f"❌ Exception in send_single_sms task for client {client_id}: {exc}", exc_info=True)
        # Log the failure explicitly
        MessageLog.objects.create(
            client=client,
            phone=client.phone,
            message=text,
            reason="Bulk SMS",
            send_status="failed",
            delivery_status="failed",
            error_details=str(exc)
        )
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_today_ground_reminders(self):
    from .utils import send_single_sms as _send_single
    from django.db import transaction
    from django.utils import timezone

    today = timezone.localdate()
    bookings = Booking.objects.filter(scheduled_date__date=today)

    if not bookings.exists():
        logger.info("📭 No bookings scheduled for today.")
        return

    logger.info(f"📅 Found {bookings.count()} bookings for today: {list(bookings.values_list('id', flat=True))}")

    for booking in bookings:
        client_service = booking.client_service
        client = client_service.client
        service = client_service.service

        # Skip if reminder already sent today
        if MessageLog.objects.filter(
                client=client,
                client_service=client_service,
                reason="Ground Service Reminder",
                timestamp__date=today
        ).exists():
            logger.info(f"⏩ Skipping client {client.id}, reminder already sent.")
            continue

        # Format time string from scheduled_date
        dt = booking.scheduled_date
        dt = timezone.make_aware(dt) if timezone.is_naive(dt) else dt
        local_dt = timezone.localtime(dt)
        time_str = local_dt.strftime("%I:%M %p")

        # Compose reminder message
        message_text = (
            f"Reminder: Our surveyor will visit you today at {time_str} "
            f"for your '{service.name}' service."
        )

        try:
            with transaction.atomic():
                status, response = _send_single(client, message_text, reason="Ground Service Reminder")

                MessageLog.objects.create(
                    client=client,
                    client_service=client_service,
                    phone=client.phone,
                    message=message_text,
                    reason="Ground Service Reminder",
                    message_id=(response or {}).get("message_id"),
                    send_status="sent" if status else "failed",
                    delivery_status="pending" if status else "failed",
                    error_details=None if status else "Unknown failure"
                )

                logger.info(f"✅ Reminder {'sent' if status else 'failed'} for client {client.id}")

        except Exception as exc:
            logger.exception(f"❌ Error sending reminder to client {client.id}")
            MessageLog.objects.create(
                client=client,
                client_service=client_service,
                phone=client.phone,
                message=message_text,
                reason="Ground Service Reminder",
                send_status="failed",
                delivery_status="failed",
                error_details=str(exc)
            )
            raise self.retry(exc=exc)
        
        
        