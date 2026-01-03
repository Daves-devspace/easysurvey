# core/tasks.py

from datetime import datetime, timedelta
import time
import logging
import uuid  # Required for generating task_ids manually

from celery import shared_task, group
from django.db import transaction
from django.utils import timezone
from django.conf import settings
from django.core.mail import send_mail, EmailMultiAlternatives
from django.template.loader import render_to_string

# Import your models
from apps.EasyDocs.models import Client, MessageLog, Booking, ScheduledTask, SiteSettings

# Import your utils (as provided in your prompt)
from apps.EasyDocs.utils import (
    update_pending_sms_logs_and_balance, 
    MobileSasaAPI, 
    personalize, 
    clean_placeholders, 
    send_company_copy_if_needed,
    send_single_sms as _send_single_util # Renamed to avoid name conflict with task
)

# Import other tasks if needed
from .files.tasks import migrate_documents_to_drive_task

__all__ = ["migrate_documents_to_drive_task"]

logger = logging.getLogger(__name__)

# Configuration
BATCH_SIZE = 50       # Client chunk size
API_CHUNK_SIZE = 20   # SMS API chunk size
INSTANCE_NAME = getattr(settings, "INSTANCE_NAME", "A")

# -----------------------------
# Celery Tasks
# -----------------------------

@shared_task
def my_task():
    logger.info(f"Executing task in instance {INSTANCE_NAME}")

@shared_task(bind=True, autoretry_for=(Exception,), retry_kwargs={'max_retries': 3})
def update_sms_delivery_and_balance(self):
    """
    Celery task wrapper for update_pending_sms_logs_and_balance with proper logging and retry.
    """
    logger.info("📩 Starting scheduled SMS delivery and balance update (task id=%s)", getattr(self.request, 'id', None))
    try:
        summary = update_pending_sms_logs_and_balance()
        logger.info(
            "✅ SMS Delivery Update Complete: Total Pending=%s, Updated=%s, Failed=%s, Balance=%s",
            summary.get('still_pending', 0), # Corrected key from utils
            summary.get('delivered', 0) + summary.get('failed', 0), # logical 'updated' count
            summary.get('failed', 0),
            summary.get('balance', 'unknown')
        )
        return {"status": "success", "summary": summary}
    except Exception as exc:
        logger.exception("⚠️ SMS Delivery Update failed: %s", exc)
        try:
            self.retry(exc=exc)
        except Exception:
            logger.exception("Retry failed or max retries exceeded for update_sms_delivery_and_balance")
            return {"status": "failed", "error": str(exc)}


@shared_task
def retry_failed_sms(log_id=None):
    """
    Retry sending a failed SMS from its MessageLog ID.
    """
    if log_id is None:
        logger.info("retry_failed_sms called without log_id — skipping.")
        return

    try:
        log = MessageLog.objects.get(id=log_id)
    except MessageLog.DoesNotExist:
        logger.warning("retry_failed_sms: MessageLog id=%s does not exist.", log_id)
        return

    client = log.client
    # If client is missing (e.g. ad-hoc SMS), we might need to handle differently, 
    # but existing logic assumes client exists for single SMS.
    if not client and log.recipient_type == 'client': 
         logger.warning("retry_failed_sms: Client missing for log id=%s", log_id)
         return

    try:
        # Use the utility directly, but we need to adapt it slightly as the util expects a Client object
        # If log has no client (e.g. employee), we construct a dummy object or handle manually.
        target_phone = log.phone
        
        # Helper to mock client if needed (duck typing)
        class MockClient:
            def __init__(self, phone): self.phone = phone
        
        target = client if client else MockClient(target_phone)

        status, response = _send_single_util(target, log.message, reason=log.reason)
        
        logger.info(f"Retried log {log_id}. New attempt status: {status}")

    except Exception as e:
        logger.exception("Retry failed for log %s: %s", log_id, e)


@shared_task(bind=True)
def _send_chunk(self, template, client_ids):
    """
    Send a chunk of personalized messages to clients.
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

    # --- Create MessageLog entries (Robust Matching) ---
    
    # Helper to normalize phone for matching (remove + or 254/0 prefix logic if needed)
    # The API util already cleans, but response might vary.
    def normalize(p):
        return str(p).replace('+', '').replace(' ', '')[-9:] if p else ''

    sent_map_norm = {normalize(k): v for k, v in sent_infos.items()}
    failed_map_norm = {normalize(k): v for k, v in failed_infos.items()}

    logs_to_create = []
    
    for phone, (client, msg) in phone_map.items():
        phone_norm = normalize(phone)
        
        info = None
        status = 'sent' # Default assumption if logic below fails, but checked
        delivery = 'pending'
        error = None
        msg_id = None
        
        if phone in sent_infos:
            info = sent_infos[phone]
        elif phone_norm in sent_map_norm:
            info = sent_map_norm[phone_norm]
        
        if info:
            msg_id = info.get('message_id')
            error = f"API raw: {info.get('raw')}"
        else:
            # Check failures
            fail_info = None
            if phone in failed_infos:
                fail_info = failed_infos[phone]
            elif phone_norm in failed_map_norm:
                fail_info = failed_map_norm[phone_norm]
            
            if fail_info:
                status = 'failed'
                delivery = 'failed'
                error = f"API send error: {fail_info.get('error')}"
            else:
                # No record found
                error = 'No provider acknowledgment in response'
                # Decide if this is sent or failed. 
                # If neither in sent nor failed list, mark as sent/pending but log the weird state
                status = 'sent'
                delivery = 'pending'
        
        logs_to_create.append(MessageLog(
            client=client,
            phone=phone,
            message=msg,
            reason='Bulk SMS broadcast',
            recipient_type='client',
            message_id=msg_id,
            send_status=status,
            delivery_status=delivery,
            error_details=error
        ))

    # --- Save logs ---
    if logs_to_create:
        try:
            MessageLog.objects.bulk_create(logs_to_create, batch_size=100)
            logger.info("_send_chunk created %s MessageLog rows", len(logs_to_create))
        except Exception as exc:
            logger.exception("Bulk create failed, saving individually: %s", exc)
            for l in logs_to_create:
                l.save()

    # --- Update ScheduledTask if present ---
    try:
        if task_id:
            # Determine overall status
            overall_status = 'completed'
            if failed_infos:
                overall_status = 'partial' if sent_infos else 'failed'

            # Only update if the ScheduledTask exists (it might be a direct call)
            ScheduledTask.objects.filter(celery_task_id=task_id).update(
                status=overall_status,
                completed_at=timezone.now()
            )
            # Fallback for older rows that might map via task_id field
            ScheduledTask.objects.filter(task_id=task_id).update(
                status=overall_status,
                completed_at=timezone.now()
            )
    except Exception:
        logger.warning("Could not update ScheduledTask status (might not exist for this run)")


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
    task_id = getattr(self.request, 'id', None)

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
                    failed_count += 1
        except Exception:
            logger.exception("Failed enumerating employee profiles")

    # Company copy
    try:
        res = send_company_copy_if_needed(template, reason='Bulk SMS broadcast')
        if res is True: sent_count += 1
    except Exception:
        logger.exception("send_company_copy_if_needed failed")

    # Mark ScheduledTask
    try:
        if task_id:
            ScheduledTask.objects.filter(celery_task_id=task_id).update(status='sent')
            ScheduledTask.objects.filter(task_id=task_id).update(status='sent')
    except Exception:
        pass

    elapsed = timezone.now() - start
    logger.info("send_employee_and_company_copy finished: sent=%s failed=%s elapsed=%s", sent_count, failed_count, elapsed)
    return {"sent": sent_count, "failed": failed_count}


@shared_task(bind=True)
def schedule_bulk_broadcast(self, template=None, scheduled_iso=None):
    """
    Schedule the bulk broadcast.
    
    CRITICAL FIX: 
    - If `scheduled_iso` is set (Future): Create ScheduledTask with status='pending'. DO NOT dispatch to Celery.
    - If `scheduled_iso` is None (Now): Dispatch to Celery immediately AND Create ScheduledTask with status='sent'.
    
    This prevents duplicates where both Celery and the DB Dispatcher try to run the task.
    """
    client_ids = list(Client.objects.values_list('id', flat=True))
    chunks = [client_ids[i:i + BATCH_SIZE] for i in range(0, len(client_ids), BATCH_SIZE)]

    eta = None
    if scheduled_iso:
        try:
            dt = datetime.fromisoformat(scheduled_iso)
            eta = timezone.make_aware(dt) if timezone.is_naive(dt) else dt
            # If ETA is in the past, treat as None (send now)
            if eta <= timezone.now():
                eta = None
        except Exception:
            logger.exception("Invalid scheduled_iso passed to schedule_bulk_broadcast; scheduling immediately")
            eta = None

    message_preview = template[:200] if template else None
    created = []

    # 1. Schedule Client Chunks
    for chunk in chunks:
        task_name = "_send_chunk"
        payload = {"template": template, "client_ids": chunk}
        
        if eta:
            # OPTION A: FUTURE SCHEDULE
            # Create DB entry only. The Dispatcher will find it later.
            st = ScheduledTask.objects.create(
                task_id=str(uuid.uuid4()), # Use UUID for DB tracking
                task_name=task_name,
                payload=payload,
                scheduled_time=eta,
                message_preview=message_preview,
                status="pending", 
            )
            created.append({"scheduled_task_id": str(st.id), "celery_id": None, "task_name": task_name})
            logger.info("Queued (DB) future task %s for %s clients at %s", st.id, len(chunk), eta)
            
        else:
            # OPTION B: SEND NOW
            # Dispatch to Celery immediately
            res = _send_chunk.apply_async(kwargs=payload)
            
            # Record it as already sent/queued
            st = ScheduledTask.objects.create(
                task_id=res.id, # Use Celery ID
                task_name=task_name,
                payload=payload,
                scheduled_time=timezone.now(),
                message_preview=message_preview,
                status="sent", 
            )
            created.append({"scheduled_task_id": str(st.id), "celery_id": res.id, "task_name": task_name})
            logger.info("Dispatched (Celery) immediate task %s for %s clients", res.id, len(chunk))


    # 2. Schedule Employee/Company Copy
    task_name_emp = "send_employee_and_company_copy"
    payload_emp = {"template": template}
    
    if eta:
        st_emp = ScheduledTask.objects.create(
            task_id=str(uuid.uuid4()),
            task_name=task_name_emp,
            payload=payload_emp,
            scheduled_time=eta,
            message_preview=message_preview,
            status="pending",
        )
        created.append({"scheduled_task_id": str(st_emp.id), "celery_id": None, "task_name": task_name_emp})
    else:
        emp_res = send_employee_and_company_copy.apply_async(kwargs=payload_emp)
        st_emp = ScheduledTask.objects.create(
            task_id=emp_res.id,
            task_name=task_name_emp,
            payload=payload_emp,
            scheduled_time=timezone.now(),
            message_preview=message_preview,
            status="sent",
        )
        created.append({"scheduled_task_id": str(st_emp.id), "celery_id": emp_res.id, "task_name": task_name_emp})

    return {"scheduled": created}


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_single_sms(self, client_id, text):
    """
    Send SMS to a single client with retry logic.
    """
    try:
        client = Client.objects.get(pk=client_id)
    except Client.DoesNotExist:
        logger.error(f"❌ Client {client_id} not found")
        return False
    
    logger.debug(f"→ send_single_sms task starting for client {client_id}")

    try:
        # Uses the util imported as _send_single_util
        # The util already created a MessageLog, so we just return the status.
        status, raw = _send_single_util(client, text, reason="Single SMS")
        logger.debug(f"   util.send_single_sms returned status={status}")
        return status

    except Exception as exc:
        logger.error(f"❌ Exception in send_single_sms task for client {client_id}: {exc}", exc_info=True)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_today_ground_reminders(self):
    """
    Send ground service reminders for bookings scheduled today.
    """
    today = timezone.localdate()
    bookings = Booking.objects.filter(scheduled_date__date=today)

    if not bookings.exists():
        logger.info("📭 No bookings scheduled for today.")
        return

    logger.info(f"📅 Found {bookings.count()} bookings for today")

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
            continue

        dt = booking.scheduled_date
        dt = timezone.make_aware(dt) if timezone.is_naive(dt) else dt
        local_dt = timezone.localtime(dt)
        time_str = local_dt.strftime("%I:%M %p")

        message_text = (
            f"Reminder: Our surveyor will visit you today at {time_str} "
            f"for your '{service.name}' service."
        )

        try:
            _send_single_util(client, message_text, reason="Ground Service Reminder")
        except Exception as exc:
            logger.exception(f"❌ Error sending reminder to client {client.id}")


# -----------------------------
# Dispatcher
# -----------------------------

DISPATCH_MAP = {
    "_send_chunk": _send_chunk,
    "send_employee_and_company_copy": send_employee_and_company_copy,
    "send_single_sms": send_single_sms,
    "send_today_ground_reminders": send_today_ground_reminders,
    "update_sms_delivery_and_balance": update_sms_delivery_and_balance,
}

@shared_task
def dispatch_due_scheduled_tasks():
    """
    Dispatch all scheduled tasks that are due.
    """
    now = timezone.now()
    logger.info("🕒 dispatch_due_scheduled_tasks starting at %s", now)

    TASKS_REQUIRING_PAYLOAD = {
        "_send_chunk",
        "send_employee_and_company_copy",
        "send_single_sms"
    }
    
    BATCH = 200
    total_dispatched = 0

    with transaction.atomic():
        due_qs = (
            ScheduledTask.objects
            .select_for_update(skip_locked=True)
            .filter(scheduled_time__lte=now, status='pending')
            .order_by('scheduled_time')[:BATCH]
        )

        for scheduled in due_qs:
            try:
                task_name = scheduled.task_name
                payload = scheduled.payload or {}
                task_callable = DISPATCH_MAP.get(task_name)

                if not task_callable:
                    scheduled.status = "failed"
                    scheduled.save(update_fields=["status"])
                    continue

                if task_name in TASKS_REQUIRING_PAYLOAD and not payload:
                     scheduled.status = "failed"
                     scheduled.save(update_fields=["status"])
                     continue

                # Dispatch
                if payload:
                    res = task_callable.apply_async(kwargs=payload)
                else:
                    res = task_callable.apply_async()

                if getattr(res, "id", None):
                    scheduled.status = "sent"
                    scheduled.task_id = res.id
                    scheduled.save(update_fields=["status", "task_id"])
                    total_dispatched += 1

            except Exception as exc:
                logger.exception("Error dispatching task %s", scheduled.id)

    logger.info("🏁 dispatch_due_scheduled_tasks completed: dispatched=%s", total_dispatched)
    return {"dispatched": total_dispatched}