# core/tasks.py
from datetime import datetime

from celery import shared_task
from django.utils import timezone

from apps.EasyDocs.models import Client, MessageLog, Booking
from apps.EasyDocs.utils import update_pending_sms_logs_and_balance, personalize



# at the top of apps/EasyDocs/tasks.py
import logging
logger = logging.getLogger(__name__)

@shared_task
def update_sms_delivery_and_balance():
    print("Running scheduled SMS delivery and balance update")
    update_pending_sms_logs_and_balance()


# tasks.py

@shared_task
def schedule_bulk_personalized_sms(template, scheduled_iso=None):
    clients = list(Client.objects.all())
    logger.debug(f"Scheduling for {len(clients)} clients; iso={scheduled_iso}")
    for i in range(0, len(clients), 50):
        chunk = clients[i:i+50]
        for client in chunk:
            text = personalize(template, client)
            if scheduled_iso:
                eta = datetime.fromisoformat(scheduled_iso)
                # Schedule each single‐send task for that ETA
                send_single_sms.apply_async(
                    args=[client.id, text],
                    eta=eta
                )
            else:
                logger.debug(f"→ queue send_single_sms.delay for client {client.id}")
                # Fire off now
                send_single_sms.delay(client.id, text)
    return {'sent_to': len(clients)}



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

    today = timezone.localdate()
    bookings = Booking.objects.filter(scheduled_date=today)

    logger.info(f"📅 Checking for ground bookings on {today}... Found {bookings.count()}")

    for booking in bookings:
        client_service = booking.client_service
        client = client_service.client
        service = client_service.service

        time_str = booking.scheduled_time.strftime('%I:%M %p')
        text = f"Reminder: Our surveyor will visit you today at {time_str} for your '{service.name}' service."

        try:
            status, raw = _send_single(client, text, reason="Ground Service Reminder")
            logger.debug(f"→ SMS send returned status={status} for client {client.id}")

            log = MessageLog.objects.create(
                client=client,
                phone=client.phone,
                message=text,
                reason="Ground Service Reminder",
                message_id=raw.get("message_id") if raw else None,
                send_status="sent" if status else "failed",
                delivery_status="pending",
                error_details=None if status else "Unknown sending failure"
            )
            logger.info(f"✅ Reminder sent and MessageLog created for client {client.id}, log id={log.id}")

        except Exception as exc:
            logger.error(f"❌ Failed to send reminder to client {client.id}: {exc}", exc_info=True)
            MessageLog.objects.create(
                client=client,
                phone=client.phone,
                message=text,
                reason="Ground Service Reminder",
                send_status="failed",
                delivery_status="failed",
                error_details=str(exc)
            )
            self.retry(exc=exc)
