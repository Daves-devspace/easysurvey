# core/tasks.py

from datetime import datetime, timedelta
import time
import logging

from celery import shared_task, group
from django.db import transaction
from django.utils import timezone
from django.conf import settings
from django.core.mail import send_mail, EmailMultiAlternatives
from django.template.loader import render_to_string

from apps.EasyDocs.models import Client, MessageLog, Booking, ScheduledTask, SiteSettings
from apps.EasyDocs.utils import update_pending_sms_logs_and_balance, MobileSasaAPI, personalize,personalize, clean_placeholders, send_company_copy_if_needed
from .files.tasks import migrate_documents_to_drive_task

__all__ = ["migrate_documents_to_drive_task"]

logger = logging.getLogger(__name__)

# Configuration
BATCH_SIZE = 50  # Celery task chunk size
API_CHUNK_SIZE = 20  # SMS API chunk size (reduced from 50 for better reliability)



logger = logging.getLogger(__name__)


# -----------------------------
# Celery Task
# -----------------------------
from celery import shared_task

@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def update_sms_delivery_and_balance(self):
    """
    Celery task wrapper for update_pending_sms_logs_and_balance with proper logging and retry.
    """
    logger.info("📩 Starting scheduled SMS delivery and balance update (task id=%s)", getattr(self.request, 'id', None))
    try:
        summary = update_pending_sms_logs_and_balance()
        logger.info(
            "✅ SMS Delivery Update Complete: Total Pending=%s, Updated=%s, Failed=%s, Balance=%s",
            summary.get('total_pending', 0),
            summary.get('updated_count', 0),
            summary.get('failed_count', 0),
            summary.get('balance', {}).get('balance', 'unknown') if isinstance(summary.get('balance'), dict) else summary.get('balance')
        )
        return {"status": "success", "summary": summary}
    except Exception as exc:
        logger.exception("⚠️ SMS Delivery Update failed: %s", exc)
        try:
            self.retry(exc=exc)
        except Exception:
            logger.exception("Retry failed or max retries exceeded for update_sms_delivery_and_balance")
            return {"status": "failed", "error": str(exc)}

@shared_task(bind=True)
def realtime_update_sms_delivery_and_balance(self, interval_seconds=30, max_iterations=None):
    """
    Continuously updates pending SMS delivery statuses and fetches balance.
    - interval_seconds: delay between successive checks (default 30s)
    - max_iterations: optional cap for testing; None = infinite
    """
    logger.info("🚀 Starting real-time SMS delivery & balance updater")
    iteration = 0

    while True:
        iteration += 1
        try:
            summary = update_pending_sms_logs_and_balance()
            logger.info(
                f"✅ Iteration {iteration}: Total Pending={summary.get('total_pending', 0)}, "
                f"Updated={summary.get('updated_count', 0)}, Failed={summary.get('failed_count', 0)}, "
                f"Balance={summary.get('balance', {}).get('balance', 'unknown')}"
            )
        except Exception as exc:
            logger.exception(f"⚠️ Error in real-time SMS updater iteration {iteration}: {exc}")

        if max_iterations and iteration >= max_iterations:
            logger.info("🛑 Max iterations reached, stopping real-time updater")
            break

        time.sleep(interval_seconds)

@shared_task
def test_timezone_task():
    print("[CELERY] timezone.now():", timezone.now())  # Django-aware time
    print("[CELERY] datetime.now():", datetime.now())  # Naive system time
    print("[CELERY] datetime.utcnow():", datetime.utcnow())  # Naive UTC time
    print("[CELERY] timezone.get_current_timezone():", timezone.get_current_timezone())  # Django timezone
    print("[CELERY] System timezone:", time.tzname)  # OS-level timezone


@shared_task
def retry_failed_sms(log_id=None):
    if log_id is None:
        logger.info("retry_failed_sms called without log_id — skipping.")
        return

    try:
        log = MessageLog.objects.get(id=log_id)
    except MessageLog.DoesNotExist:
        logger.warning("retry_failed_sms: MessageLog id=%s does not exist.", log_id)
        return

    client = log.client

    try:
        status, response = send_single_sms(client, log.message, reason=log.reason)
        log.send_status = 'sent' if status == 'sent' else 'failed'
        log.message_id = response.get('message_id', '') if isinstance(response, dict) else ''
        log.error_details = None if status == 'sent' else (response.get('message') if isinstance(response, dict) else str(response))
    except Exception as e:
        log.send_status = 'failed'
        log.error_details = str(e)

    log.save()


@shared_task(bind=True)
def _send_chunk(self, template, client_ids):
    """
    Send a chunk of personalized messages to clients.

    Fully resilient to any API weirdness.
    - Logs sent and failed messages to MessageLog.
    - Always returns a predictable dict {"sent": <count>, "failed": <count>}.
    """
    start = timezone.now()
    api = MobileSasaAPI()
    task_id = getattr(self.request, 'id', None)
    logger.info("Starting _send_chunk task id=%s for %s clients", task_id, len(client_ids))

    # --- Prepare messages ---
    message_pairs = []
    phone_map = {}  # phone -> (client, personalized_message)
    for cid in client_ids:
        try:
            client = Client.objects.get(pk=cid)
            msg = personalize(template, client)
            phone = client.phone
            if not phone:
                logger.warning("Skipping client id=%s (no phone)", cid)
                continue
            message_pairs.append({'phone': phone, 'message': msg})
            phone_map[phone] = (client, msg)
        except Client.DoesNotExist:
            logger.warning("Client id %s does not exist - skipping", cid)
        except Exception as exc:
            logger.exception("Error preparing message for client id=%s: %s", cid, exc)

    sent_infos = {}   # phone -> {"message_id":..., "raw":...}
    failed_infos = {} # phone -> {"error":..., "raw":...}

    # --- Send in chunks ---
    for i in range(0, len(message_pairs), API_CHUNK_SIZE):
        chunk = message_pairs[i:i + API_CHUNK_SIZE]
        try:
            resp = api.send_personalized_sms(chunk)
            raw_resp = resp.get('raw') if isinstance(resp, dict) else resp
            logger.debug("_send_chunk API response: %s", raw_resp)

            # Process sent messages
            for s in resp.get('sent', []):
                if not isinstance(s, dict):
                    s = {"phone": s if isinstance(s, str) else None, "message_id": None, "raw": s}
                phone = s.get('phone')
                mid = s.get('message_id') or s.get('messageId') or s.get('id') or None
                if phone:
                    sent_infos[phone] = {"message_id": mid, "raw": s.get('raw', s)}

            # Process failed messages
            for f in resp.get('failed', []):
                if not isinstance(f, dict):
                    f = {"phone": None, "error": str(f), "raw": f}
                phone = f.get('phone')
                err = f.get('error') or f.get('message') or 'provider_rejected'
                if phone:
                    failed_infos[phone] = {"error": err, "raw": f.get('raw', f)}

        except Exception as exc:
            logger.exception("API send_personalized_sms failed for chunk: %s", exc)
            for it in chunk:
                failed_infos[it['phone']] = {"error": f"exception: {exc}", "raw": None}

        # Mild rate-limit pause
        time.sleep(0.3)

    # --- Create MessageLog entries ---
    logs_to_create = []
    for phone, (client, msg) in phone_map.items():
        if phone in sent_infos:
            info = sent_infos[phone]
            logs_to_create.append(MessageLog(
                client=client,
                phone=phone,
                message=msg,
                reason='Bulk SMS broadcast',
                recipient_type='client',
                message_id=info.get('message_id'),
                send_status='sent',
                delivery_status='pending',
                error_details=f"API send raw: {info.get('raw')}"
            ))
        elif phone in failed_infos:
            info = failed_infos[phone]
            logs_to_create.append(MessageLog(
                client=client,
                phone=phone,
                message=msg,
                reason='Bulk SMS broadcast',
                recipient_type='client',
                message_id=None,
                send_status='failed',
                delivery_status='failed',
                error_details=f"API send error: {info.get('error')}"
            ))
        else:
            # Rare case: neither sent nor failed
            logs_to_create.append(MessageLog(
                client=client,
                phone=phone,
                message=msg,
                reason='Bulk SMS broadcast',
                recipient_type='client',
                message_id=None,
                send_status='sent',
                delivery_status='pending',
                error_details='No provider acknowledgment in response'
            ))

    # --- Save logs ---
    if logs_to_create:
        try:
            MessageLog.objects.bulk_create(logs_to_create, batch_size=100)
            logger.info("_send_chunk created %s MessageLog rows", len(logs_to_create))
        except Exception as exc:
            logger.exception("Bulk create failed, saving individually: %s", exc)
            created = 0
            for l in logs_to_create:
                try:
                    l.save()
                    created += 1
                except Exception as e:
                    logger.exception("Saving individual MessageLog failed: %s", e)
            logger.info("Individually saved %s MessageLog rows", created)

    # --- Update ScheduledTask if present ---
    try:
        if task_id:
            ScheduledTask.objects.filter(task_id=task_id).update(status='sent')
    except Exception:
        logger.exception("Failed updating ScheduledTask for _send_chunk")

    elapsed = timezone.now() - start
    logger.info("_send_chunk finished: sent=%s failed=%s elapsed=%s",
                len(sent_infos), len(failed_infos), elapsed)

    return {"sent": len(sent_infos), "failed": len(failed_infos)}


@shared_task(bind=True)
def send_employee_and_company_copy(self, template):
    """
    Send cleaned message to employees (if enabled) and one company copy.
    """
    start = timezone.now()
    api = MobileSasaAPI()
    settings = SiteSettings.objects.first()
    cleaned = clean_placeholders(template)

    sent_count = 0
    failed_count = 0

    # Employees
    if settings and settings.allow_employee_sms:
        try:
            from apps.Employee.models import EmployeeProfile
            qs = EmployeeProfile.objects.exclude(phone_number__isnull=True).exclude(phone_number='')
            if settings.employee_sms_roles:
                qs = qs.filter(role__in=settings.employee_sms_roles)

            for emp in qs:
                try:
                    resp = api.send_sms(emp.phone_number, cleaned)
                    status = resp.get('status') if isinstance(resp, dict) else False
                    message_id = None
                    if isinstance(resp, dict):
                        message_id = resp.get('message_id') or resp.get('messageId') or None

                    MessageLog.objects.create(
                        client=None,
                        phone=emp.phone_number,
                        message=cleaned,
                        reason='Bulk SMS broadcast',
                        recipient_type='employee',
                        message_id=message_id,
                        is_company_copy=False,
                        send_status='sent' if status else 'failed',
                        delivery_status='pending' if status else 'failed',
                        error_details=f"API raw: {resp}"
                    )
                    if status:
                        sent_count += 1
                    else:
                        failed_count += 1
                except Exception as exc:
                    logger.exception("Failed sending employee sms to %s: %s", emp.id, exc)
                    MessageLog.objects.create(
                        client=None,
                        phone=emp.phone_number,
                        message=cleaned,
                        reason='Bulk SMS broadcast',
                        recipient_type='employee',
                        send_status='failed',
                        delivery_status='failed',
                        error_details=str(exc)
                    )
                    failed_count += 1
        except Exception:
            logger.exception("Failed enumerating employee profiles")

    # Company copy
    try:
        send_company_copy_if_needed(template, reason='Bulk SMS broadcast')
    except Exception:
        logger.exception("send_company_copy_if_needed failed")

    # Mark ScheduledTask for this send_employee_and_company_copy as sent
    try:
        ScheduledTask.objects.filter(task_id=getattr(self.request, 'id', None)).update(status='sent')
    except Exception:
        logger.exception("Failed updating ScheduledTask for send_employee_and_company_copy")

    elapsed = timezone.now() - start
    logger.info("send_employee_and_company_copy finished: sent=%s failed=%s elapsed=%s", sent_count, failed_count, elapsed)
    return {"sent": sent_count, "failed": failed_count}


@shared_task(bind=True)
def schedule_bulk_broadcast(self, template=None, scheduled_iso=None):
    """
    Splits clients into chunks and schedules _send_chunk (and the employee/company task)
    Always stores ScheduledTask rows for the scheduled celery jobs for visibility and cancellation.
    """
    client_ids = list(Client.objects.values_list('id', flat=True))
    chunks = [client_ids[i:i + BATCH_SIZE] for i in range(0, len(client_ids), BATCH_SIZE)]

    eta = None
    if scheduled_iso:
        try:
            dt = datetime.fromisoformat(scheduled_iso)
            eta = timezone.make_aware(dt) if timezone.is_naive(dt) else dt
        except Exception:
            eta = None

    scheduled_ids = []
    for chunk in chunks:
        res = _send_chunk.apply_async(args=[template, chunk], eta=eta)
        scheduled_time = eta or timezone.now()
        try:
            ScheduledTask.objects.create(
                task_id=res.id,
                task_name='_send_chunk',
                scheduled_time=scheduled_time,
                message_preview=(template[:100] if template else ''),
                status='pending' if eta else 'sent'
            )
        except Exception:
            logger.exception("Failed creating ScheduledTask for chunk job %s", getattr(res, 'id', None))
        scheduled_ids.append(res.id)

    # schedule employee/company copy
    emp_res = send_employee_and_company_copy.apply_async(args=[template], eta=eta)
    try:
        ScheduledTask.objects.create(
            task_id=emp_res.id,
            task_name='send_employee_and_company_copy',
            scheduled_time=(eta or timezone.now()),
            message_preview=(template[:100] if template else ''),
            status='pending' if eta else 'sent'
        )
    except Exception:
        logger.exception("Failed creating ScheduledTask for employee/company job %s", getattr(emp_res, 'id', None))

    return {"task_ids": scheduled_ids, "chunks": len(chunks)}






@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_single_sms(self, client_id, text):
    """
    Send SMS to a single client with retry logic.
    Used for individual notifications, not bulk broadcasts.
    """
    from .utils import send_single_sms as _send_single
    from .models import MessageLog, Client

    try:
        client = Client.objects.get(pk=client_id)
    except Client.DoesNotExist:
        logger.error(f"❌ Client {client_id} not found")
        return False
    
    logger.debug(f"→ send_single_sms task starting for client {client_id}")

    try:
        status, raw = _send_single(client, text, reason="Single SMS")
        logger.debug(f"   util.send_single_sms returned status={status}")

        log = MessageLog.objects.create(
            client=client,
            phone=client.phone,
            message=text,
            reason="Single SMS",
            message_id=raw.get("message_id") if raw else None,
            send_status="sent" if status else "failed",
            delivery_status="pending" if status else "failed",
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
            reason="Single SMS",
            send_status="failed",
            delivery_status="failed",
            error_details=str(exc)
        )
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_today_ground_reminders(self):
    """
    Send ground service reminders for bookings scheduled today.
    Runs daily via Celery Beat scheduler.
    """
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