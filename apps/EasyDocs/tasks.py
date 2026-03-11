from datetime import datetime, timedelta
import time
import logging
import uuid
from celery import shared_task, group
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.core.cache import cache
from django.conf import settings
from apps.EasyDocs.models import Client, MessageLog, Booking, ScheduledTask, SiteSettings
from apps.EasyDocs.utils import (
    update_pending_sms_logs_and_balance, 
    MobileSasaAPI, 
    personalize, 
    clean_placeholders, 
    send_company_copy_if_needed,
    send_single_sms as _send_single_util
)

logger = logging.getLogger(__name__)
BATCH_SIZE = 50
API_CHUNK_SIZE = 20

INSTANCE_NAME = getattr(settings, "INSTANCE_NAME", "A")

# -----------------------------
# Celery Tasks
# -----------------------------

def disable_cache_invalidation(func):
    """Prevent excessive cache invalidation in Celery tasks"""
    def wrapper(*args, **kwargs):
        # Temporarily disable cache
        original_cache = cache
        cache._cache = {}
        try:
            return func(*args, **kwargs)
        finally:
            cache._cache = original_cache._cache
    return wrapper


@shared_task
def my_task():
    logger.info(f"Executing task in instance {INSTANCE_NAME}")


@shared_task(bind=True, autoretry_for=(Exception,), retry_kwargs={'max_retries': 3})
def update_sms_delivery_and_balance(self):
    logger.info("📩 Starting DLR update...")
    try:
        summary = update_pending_sms_logs_and_balance()
        return {"status": "success", "summary": summary}
    except Exception as exc:
        logger.exception("⚠️ DLR Update failed")
        raise self.retry(exc=exc)

@shared_task
def retry_failed_sms(log_id=None):
    if not log_id: return
    try:
        log = MessageLog.objects.get(id=log_id)
        class MockClient:
            def __init__(self, phone): self.phone = phone
        target = log.client if log.client else MockClient(log.phone)
        status, response = _send_single_util(target, log.message, reason=log.reason)
        logger.info(f"Retried log {log_id}. Status: {status}")
    except Exception as e:
        logger.exception(f"Retry failed for {log_id}: {e}")

@shared_task(bind=True)
@disable_cache_invalidation
def _send_chunk(self, template, client_ids):
    """
    Send a chunk of personalized messages to clients.
    """
    start = timezone.now()
    api = MobileSasaAPI()
    task_id = getattr(self.request, 'id', None)
    
    # Prepare messages
    message_pairs = []
    phone_map = {}
    
    for cid in client_ids:
        try:
            client = Client.objects.get(pk=cid)
            msg = personalize(template, client)
            # FIX: Check for empty personalized messages
            if not msg or not msg.strip():
                continue
                
            phone = client.phone
            if phone:
                message_pairs.append({'phone': phone, 'message': msg})
                phone_map[phone] = (client, msg)
        except Client.DoesNotExist:
            continue

    sent_infos = {}
    failed_infos = {}

    # Send via API
    for i in range(0, len(message_pairs), API_CHUNK_SIZE):
        chunk = message_pairs[i:i + API_CHUNK_SIZE]
        try:
            resp = api.send_personalized_sms(chunk)
            # Process sent
            for s in resp.get('sent', []):
                phone = s.get('phone')
                mid = s.get('message_id') or s.get('messageId') or s.get('id')
                if phone: sent_infos[phone] = {"message_id": mid, "raw": s}
            # Process failed
            for f in resp.get('failed', []):
                phone = f.get('phone')
                if phone: failed_infos[phone] = {"error": f.get('error'), "raw": f}
        except Exception as exc:
            for it in chunk:
                failed_infos[it['phone']] = {"error": str(exc), "raw": None}
        time.sleep(0.3)

    # Logging Logic
    logs_to_create = []
    
    def normalize(p):
        return str(p).replace('+', '').replace(' ', '')[-9:] if p else ''
    sent_map_norm = {normalize(k): v for k, v in sent_infos.items()}
    failed_map_norm = {normalize(k): v for k, v in failed_infos.items()}
    
    for phone, (client, msg) in phone_map.items():
        phone_norm = normalize(phone)
        status = 'sent'
        delivery = 'pending'
        error = None
        msg_id = None
        
        info = sent_infos.get(phone) or sent_map_norm.get(phone_norm)
        
        if info:
            msg_id = info.get('message_id')
        else:
            fail_info = failed_infos.get(phone) or failed_map_norm.get(phone_norm)
            if fail_info:
                status = 'failed'
                delivery = 'failed'
                error = f"API error: {fail_info.get('error')}"
            else:
                pass

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

    if logs_to_create:
        MessageLog.objects.bulk_create(logs_to_create, batch_size=100)

    # Update Task Status
    if task_id:
        final_status = 'completed' if not failed_infos else ('partial' if sent_infos else 'failed')
        ScheduledTask.objects.filter(Q(celery_task_id=task_id) | Q(task_id=task_id)).update(
            status=final_status, completed_at=timezone.now()
        )

    return {"sent": len(sent_infos), "failed": len(failed_infos)}

@shared_task(bind=True)
def send_employee_and_company_copy(self, template):
    """
    Send cleaned message to employees and company copy.
    """
    start = timezone.now()
    api = MobileSasaAPI()
    settings = SiteSettings.objects.first()
    
    cleaned = clean_placeholders(template)
    if not cleaned or not cleaned.strip():
        logger.warning("Skipping employee/company SMS: Empty message.")
        return {"sent": 0, "failed": 0, "status": "skipped_empty"}

    task_id = getattr(self.request, 'id', None)
    sent_count = 0
    failed_count = 0
    
    # 1. Employees
    if settings and settings.allow_employee_sms:
        try:
            from apps.Employee.models import EmployeeProfile
            qs = EmployeeProfile.objects.exclude(phone_number__isnull=True).exclude(phone_number='')
            if settings.employee_sms_roles:
                qs = qs.filter(role__in=settings.employee_sms_roles)
            
            for emp in qs:
                resp = api.send_sms(emp.phone_number, cleaned)
                status = resp.get('status', False)
                message_id = resp.get('message_id') if isinstance(resp, dict) else None
                
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
                    error_details=None if status else f"API: {resp.get('message')}"
                )
                if status: sent_count += 1
                else: failed_count += 1
        except Exception as e:
            logger.exception("Employee SMS failed")

    # 2. Company Copy
    try:
        resp = send_company_copy_if_needed(template)
        if resp:
            status = resp.get('status', False)
            message_id = resp.get('message_id')
            
            MessageLog.objects.create(
                client=None,
                phone=settings.company_phone,
                message=cleaned,
                reason='Bulk SMS broadcast',
                recipient_type='company',
                message_id=message_id,
                is_company_copy=True,
                send_status='sent' if status else 'failed',
                delivery_status='pending' if status else 'failed',
                error_details=None if status else f"API: {resp.get('message')}"
            )
            if status: sent_count += 1
            else: failed_count += 1
    except Exception as e:
        logger.exception("Company copy failed")

    if task_id:
        ScheduledTask.objects.filter(Q(celery_task_id=task_id) | Q(task_id=task_id)).update(status='sent')

    return {"sent": sent_count, "failed": failed_count}

@shared_task(bind=True)
def schedule_bulk_broadcast(self, template=None, scheduled_iso=None):
    """
    CRITICAL FIX: Prevent double-scheduling (DB + Celery).
    """
    client_ids = list(Client.objects.values_list('id', flat=True))
    chunks = [client_ids[i:i + BATCH_SIZE] for i in range(0, len(client_ids), BATCH_SIZE)]
    
    eta = None
    if scheduled_iso:
        try:
            dt = datetime.fromisoformat(scheduled_iso)
            eta = timezone.make_aware(dt) if timezone.is_naive(dt) else dt
            if eta <= timezone.now(): eta = None
        except: eta = None

    message_preview = template[:200] if template else None
    created = []

    # 1. Client Chunks
    for chunk in chunks:
        task_name = "_send_chunk"
        payload = {"template": template, "client_ids": chunk}
        
        if eta:
            # OPTION A: Future - DB ONLY (Dispatcher will handle it)
            st = ScheduledTask.objects.create(
                task_id=str(uuid.uuid4()), task_name=task_name, payload=payload,
                scheduled_time=eta, message_preview=message_preview, status="pending"
            )
            created.append(st.id)
        else:
            # OPTION B: Now - Celery ONLY (Mark DB as sent)
            res = _send_chunk.apply_async(kwargs=payload)
            ScheduledTask.objects.create(
                task_id=res.id, task_name=task_name, payload=payload,
                scheduled_time=timezone.now(), message_preview=message_preview, status="sent"
            )

    # 2. Employee/Company
    task_name_emp = "send_employee_and_company_copy"
    payload_emp = {"template": template}
    
    if eta:
        ScheduledTask.objects.create(
            task_id=str(uuid.uuid4()), task_name=task_name_emp, payload=payload_emp,
            scheduled_time=eta, message_preview=message_preview, status="pending"
        )
    else:
        emp_res = send_employee_and_company_copy.apply_async(kwargs=payload_emp)
        ScheduledTask.objects.create(
            task_id=emp_res.id, task_name=task_name_emp, payload=payload_emp,
            scheduled_time=timezone.now(), message_preview=message_preview, status="sent"
        )

    return {"scheduled_count": len(created)}

@shared_task(bind=True)
def send_single_sms(self, client_id, text):
    try:
        client = Client.objects.get(pk=client_id)
        status, _ = _send_single_util(client, text, reason="Single SMS")
        return status
    except Exception as exc:
        raise self.retry(exc=exc)

@shared_task(bind=True)
def send_today_ground_reminders(self):
    today = timezone.localdate()
    bookings = Booking.objects.filter(scheduled_date__date=today)
    for booking in bookings:
        client = booking.client_service.client
        if MessageLog.objects.filter(client=client, reason="Ground Service Reminder", timestamp__date=today).exists():
            continue
        msg = f"Reminder: Visit today for {booking.client_service.service.name}"
        _send_single_util(client, msg, reason="Ground Service Reminder")

DISPATCH_MAP = {
    "_send_chunk": _send_chunk,
    "send_employee_and_company_copy": send_employee_and_company_copy,
    "send_single_sms": send_single_sms,
    "send_today_ground_reminders": send_today_ground_reminders,
    "update_sms_delivery_and_balance": update_sms_delivery_and_balance,
}

# @shared_task
# def dispatch_due_scheduled_tasks():
#     now = timezone.now()
#     # FIX: Strict Zombie Protection. 
#     # Do not execute tasks that are more than 1 hour overdue.
#     # This prevents yesterday's stuck tasks from suddenly sending today.
#     zombie_threshold = now - timedelta(hours=1)
    
#     with transaction.atomic():
#         # First, mark overdue pending tasks as expired.
#         ScheduledTask.objects.filter(
#             status='pending',
#             scheduled_time__lt=zombie_threshold
#         ).update(status='expired')

#         # Now pick up legitimate tasks (scheduled <= now AND scheduled >= 1 hour ago)
#         due_qs = ScheduledTask.objects.select_for_update(skip_locked=True).filter(
#             scheduled_time__lte=now, 
#             status='pending'
#         ).order_by('scheduled_time')[:200]
        
#         for scheduled in due_qs:
#             task_callable = DISPATCH_MAP.get(scheduled.task_name)
#             if task_callable:
#                 res = task_callable.apply_async(kwargs=scheduled.payload or {})
#                 scheduled.status = "sent"
#                 scheduled.task_id = res.id
#                 scheduled.save()
@shared_task
def dispatch_due_scheduled_tasks():
    now = timezone.now()
    zombie_threshold = now - timedelta(hours=1)
    
    with transaction.atomic():
        # Mark expired
        ScheduledTask.objects.filter(
            status='pending',
            scheduled_time__lt=zombie_threshold
        ).update(status='expired')

        # Reduce from 200 to 50 per run
        due_qs = ScheduledTask.objects.select_for_update(skip_locked=True).filter(
            scheduled_time__lte=now, 
            status='pending'
        ).order_by('scheduled_time')[:50]  # Changed from 200 to 50
        
        for scheduled in due_qs:
            task_callable = DISPATCH_MAP.get(scheduled.task_name)
            if task_callable:
                res = task_callable.apply_async(kwargs=scheduled.payload or {})
                scheduled.status = "sent"
                scheduled.task_id = res.id
                scheduled.save()

@shared_task(bind=True, autoretry_for=(Exception,), retry_kwargs={'max_retries': 3})
def check_and_escalate_expired_handoffs(self):
    """
    Celery task to check for expired document handoffs and escalate them.
    Should be run periodically (e.g., every hour).
    """
    try:
        from apps.EasyDocs.services.handoffs import check_expired_handoffs, escalate_handoff
        
        expired_handoffs = check_expired_handoffs()
        escalated_count = 0
        
        for handoff in expired_handoffs:
            try:
                result = escalate_handoff(handoff)
                if result['success']:
                    escalated_count += 1
                    logger.info(
                        f"Escalated expired handoff ID {handoff.id} "
                        f"(Document: {handoff.content_type.model} #{handoff.object_id}, "
                        f"Assigned to: {handoff.assigned_to.username})"
                    )
                else:
                    logger.warning(
                        f"Failed to escalate handoff ID {handoff.id}: {result.get('message', 'Unknown error')}"
                    )
            except Exception as e:
                logger.exception(f"Error escalating handoff ID {handoff.id}: {e}")
                continue
        
        if escalated_count > 0:
            logger.info(f"Successfully escalated {escalated_count} expired document handoffs")
        else:
            logger.debug("No expired document handoffs to escalate")
        
        return {
            'success': True,
            'escalated_count': escalated_count,
            'total_expired': expired_handoffs.count()
        }
    
    except Exception as exc:
        logger.exception(f"Error in check_and_escalate_expired_handoffs task: {exc}")
        raise self.retry(exc=exc, countdown=300)  # Retry after 5 minutes
