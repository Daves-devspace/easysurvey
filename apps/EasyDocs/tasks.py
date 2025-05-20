# core/tasks.py
from datetime import datetime

from celery import shared_task, group
from django.db import transaction
from django.utils import timezone

from apps.EasyDocs.models import Client, MessageLog, Booking, ScheduledTask
from apps.EasyDocs.utils import update_pending_sms_logs_and_balance,MobileSasaAPI, personalize



# at the top of apps/EasyDocs/tasks.py
import logging
logger = logging.getLogger(__name__)

@shared_task
def update_sms_delivery_and_balance():
    print("Running scheduled SMS delivery and balance update")
    update_pending_sms_logs_and_balance()


# tasks.py



BATCH_SIZE = 50


@shared_task
def _send_chunk(template, client_ids):
    api = MobileSasaAPI()
    pairs = []
    for cid in client_ids:
        c = Client.objects.get(pk=cid)
        pairs.append({'phone': c.phone, 'message': personalize(template, c)})
    return api.send_personalized_sms(pairs)



@shared_task
def schedule_bulk_broadcast(template, scheduled_iso=None):
    """
    Chunk client IDs, schedule each _send_chunk as its own Celery task,
    track each in ScheduledTask, and return their task IDs.
    """
    client_ids = list(Client.objects.values_list('id', flat=True))
    chunks = [client_ids[i : i + BATCH_SIZE] for i in range(0, len(client_ids), BATCH_SIZE)]

    scheduled_ids = []

    for chunk in chunks:
        if scheduled_iso:
            eta = datetime.fromisoformat(scheduled_iso)
            if timezone.is_naive(eta):
                eta = timezone.make_aware(eta, timezone.get_current_timezone())
            # schedule for future
            result = _send_chunk.apply_async(args=[template, chunk], eta=eta)
            status = 'pending'
            scheduled_time = eta
        else:
            # send immediately
            result = _send_chunk.apply_async(args=[template, chunk])
            status = 'sent'
            scheduled_time = timezone.now()

        # record in ScheduledTask
        ScheduledTask.objects.create(
            task_id=result.id,
            task_name='_send_chunk',
            scheduled_time=scheduled_time,
            message_preview=template[:100],
            status=status
        )

        scheduled_ids.append(result.id)

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
    booking_ids = list(bookings.values_list("id", flat=True))
    logger.info(f"📅 Found {len(booking_ids)} bookings for today: {booking_ids}")

    if not bookings:
        return

    for booking in bookings:
        client = booking.client_service.client
        service = booking.client_service.service

        # derive time string from scheduled_date
        dt = booking.scheduled_date
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        local_dt = timezone.localtime(dt)
        time_str = local_dt.strftime("%I:%M %p")

        text = (
            f"Reminder: Our surveyor will visit you today at {time_str} "
            f"for your '{service.name}' service."
        )

        with transaction.atomic():
            try:
                status, raw = _send_single(client, text,
                                           reason="Ground Service Reminder")
                log = MessageLog.objects.create(
                    client=client,
                    phone=client.phone,
                    message=text,
                    reason="Ground Service Reminder",
                    message_id=(raw or {}).get("message_id"),
                    send_status="sent" if status else "failed",
                    delivery_status="pending",
                    error_details=None if status else "Unknown sending failure"
                )
                logger.info(f"✅ Reminder sent and log id={log.id} for client {client.id}")

            except Exception as exc:
                logger.exception(f"❌ Failed reminder for client {client.id}")
                MessageLog.objects.create(
                    client=client,
                    phone=client.phone,
                    message=text,
                    reason="Ground Service Reminder",
                    send_status="failed",
                    delivery_status="failed",
                    error_details=str(exc)
                )
                raise self.retry(exc=exc)
