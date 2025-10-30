from decimal import Decimal
import logging

from django.db.models.signals import post_save, post_delete, pre_delete
from django.dispatch import receiver
from django.utils import timezone
from django.db import transaction   
from django.core.exceptions import ObjectDoesNotExist   

from apps.EasyDocs.communication import send_and_log_sms
from apps.EasyDocs.models import (
    ClientServiceProcess, TitleDeedCollection, ClientService,
    Process, Payment, PaymentHistory, ServiceCategory, ClientSubService, Booking, Expense
)
from apps.accounts.services.cashbook import record_cash_in, record_cash_out_expense
  

logger = logging.getLogger(__name__)

import threading
from functools import wraps

_signal_lock = threading.local()
# apps/EasyDocs/signals.py
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.apps import apps
from crum import get_current_user  # handy for tracking the current user



def get_user():
    user = get_current_user()
    if user and user.is_authenticated:
        return user
    return None


@receiver(post_save)
def log_model_save(sender, instance, created, **kwargs):
    """
    Safe AuditLog writer that:
    - Skips writing while DB/migrations are running (table not present),
    - Avoids infinite loop without importing AuditLog at module import time,
    - Defensively handles errors so migrations/tests don't fail.
    """

    # 1) Skip AuditLog itself (avoid infinite loop) by checking model_name/app_label
    if getattr(sender, "_meta", None):
        if sender._meta.app_label == "easydocs" and sender._meta.model_name == "auditlog":
            return

        # Skip Django internal models
        if sender._meta.app_label in {"sessions", "admin", "contenttypes", "auth"}:
            return

    # 2) Quick DB readiness check: ensure audit table exists before attempting writes
    try:
        from django.db import connection, ProgrammingError, OperationalError
        tables = connection.introspection.table_names()
    except (ProgrammingError, OperationalError, Exception) as e:
        logger.debug("Skipping AuditLog write: DB not ready (%s).", e)
        return

    audit_table_name = "easydocs_auditlog"
    if audit_table_name not in tables:
        logger.debug("Skipping AuditLog write: table %s not present yet.", audit_table_name)
        return

    # 3) Safe local import of AuditLog and write the record
    try:
        from .models import AuditLog  # local import to avoid import-time side-effects
    except Exception as e:
        logger.warning("Could not import AuditLog model; skipping audit write (%s).", e)
        return

    # 4) Resolve user safely (get_user may raise or be unavailable in this context)
    try:
        user = get_user()
    except Exception:
        user = None

    # 5) Create audit entry inside try/except so any DB race doesn't break migrations/tests
    try:
        AuditLog.objects.create(
            user=user,
            action="create" if created else "update",
            model_name=sender.__name__ if hasattr(sender, "__name__") else str(sender),
            object_id=getattr(instance, "pk", None),
            description=f"{'Created' if created else 'Updated'} {sender.__name__ if hasattr(sender, '__name__') else sender} #{getattr(instance, 'pk', None)}",
        )
    except (ProgrammingError, OperationalError) as db_err:
        logger.warning("Failed to write AuditLog due to DB error (possibly race during migrations): %s", db_err)
    except Exception as exc:
        logger.exception("Unexpected error while writing AuditLog (skipping). %s", exc)


@receiver(post_delete)
def log_model_delete(sender, instance, **kwargs):
    from .models import AuditLog  # local import to avoid import-time side-effects
    if sender == AuditLog:
        return
    
    if sender._meta.app_label in ["sessions", "admin", "contenttypes", "auth"]:
        return
    
    AuditLog.objects.create(
        user=get_user(),
        action="delete",
        model_name=sender.__name__,
        object_id=instance.pk,
        description=f"Deleted {sender.__name__} #{instance.pk}",
    )






@receiver(post_save, sender=Payment)
def handle_payment_cashbook(sender, instance, created, **kwargs):
    """
    When a client payment is received, record a Cash IN.
    """
    if created:
        user = getattr(instance, "received_by", None)  # optional, if you later add the field
        record_cash_in(instance, user)

@receiver(post_save, sender=Expense)
def handle_expense_cashbook(sender, instance, created, **kwargs):
    if not created:
        return

    user = getattr(instance, "created_by", None)
    try:
        record_cash_out_expense(instance.description, instance.amount, user)
    except ValueError as e:
        # Log for debugging / audit
        logger.error(f"Cashbook entry not created for Expense[{instance.pk}]: {e}")
        
        # Optional: attach an error flag on the Expense (so frontend/admin can show it)
        instance.cashbook_error = str(e)
        Expense.objects.filter(pk=instance.pk).update(notes=f"Cashbook error: {e}")

        
        
        
def prevent_recursion(key_func=None):
    """
    Prevents recursive execution of a signal handler.
    Includes logging for debug purposes.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = key_func(*args, **kwargs) if key_func else func.__name__

            if not hasattr(_signal_lock, 'active_signals'):
                _signal_lock.active_signals = set()

            if key in _signal_lock.active_signals:
                logger.warning(f"[prevent_recursion] Skipping recursive signal: {key}")
                return

            logger.debug(f"[prevent_recursion] Entering signal: {key}")
            _signal_lock.active_signals.add(key)
            try:
                return func(*args, **kwargs)
            finally:
                _signal_lock.active_signals.remove(key)
                logger.debug(f"[prevent_recursion] Exiting signal: {key}")

        return wrapper
    return decorator


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

# signals.py

@receiver(post_save, sender=ClientService)
def client_service_created_handler(sender, instance, created, **kwargs):
    # if not created:
    #     return

    service, client = instance.service, instance.client

    # TITLE‐category: create the process entries & send SMS for the first one
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
                send_process_sms(
                    instance,
                    client,
                    client.phone,
                    process.message,
                    reason
                )





@receiver(post_save, sender=Booking)
def booking_created_handler(sender, instance, created, **kwargs):
    # Only on creation (not on every save)
    if not created:
        return  # Do not proceed if this is just an update
    from django.db import connection
    print(f"📦 Booking Signal Triggered: created={created} | id={instance.id}")

    cs      = instance.client_service
    service = cs.service
    client  = cs.client

    # Only for Ground‐category services
    if service.category != ServiceCategory.GROUND:
        return

    reason  = f"{service.name} – booking scheduled"
    message = instance.dispatch_message

    send_process_sms(
        client_service=cs,
        client=client,
        phone=client.phone,
        message=message,
        reason=reason
    )















def update_full_total(client_service: ClientService):
    """
    Recalculate and persist the full_total_price for a given ClientService.
    """
    try:
        total = client_service._calculate_full_total()
        ClientService.objects.filter(id=client_service.id).update(full_total_price=total)
        logger.info(
            f"Recalculated full_total_price for ClientService #{client_service.pk}: {client_service.full_total_price}")
    except Exception as e:
        logger.error(f"Failed to update full_total_price for ClientService #{client_service.pk}: {e}", exc_info=True)



@receiver(post_save, sender=ClientService)
@prevent_recursion(lambda sender, instance, **kwargs: f"client_service:{instance.pk}")
def on_client_service_change(sender, instance: ClientService, created, **kwargs):
    """
    Whenever a ClientService is saved—whether from overrides or any other edit—
    re‑compute and persist its full_total_price.
    """
    # Skip the very first save (when created) because .save() override already did it
    if created:
        return

    # Now that overridden_total_price or land_description or whatever may have changed,
    # recalc the full total using your helper (which does a .save(update_fields=['full_total_price']))
    update_full_total(instance)



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
    # if instance.id_number:
    #     msg += f" (ID: {instance.id_number})"

    reason = f"{svc.service.name} – deed collected"
    send_process_sms(svc, svc.client, svc.client.phone, msg, reason)




@receiver(post_save, sender=Payment)
def allocate_payment(sender, instance, created, **kwargs):
    """
    Triggered when a Payment is created.
    Allocation priority:
      1️⃣ Settle ClientService processes first (by process__step_order)
      2️⃣ Then subservices (by added_on oldest first)
    Each subservice calculates profit independently from its base and overridden prices.
    """
    if not created:
        return

    try:
        with transaction.atomic():
            logger.info(f"💰 Payment #{instance.id} received: KES {instance.amount} for ClientService #{instance.client_service_id}")

            # Lock the parent ClientService
            cs = (
                ClientService.objects
                .select_for_update()
                .select_related('service')
                .get(pk=instance.client_service_id)
            )
            logger.info(f"🔒 Locked ClientService #{cs.id} ({cs.service.name}) for allocation")

            # Lock related processes & subservices
            s_processes = list(
                ClientServiceProcess.objects
                .filter(client_service=cs)
                .select_for_update()
                .order_by('process__step_order')
            )
            subs = list(
                ClientSubService.objects
                .filter(client_service=cs)
                .select_for_update()
                .order_by('added_on')  # oldest first
            )

            logger.info(f"📦 Found {len(s_processes)} processes and {len(subs)} subservices for ClientService #{cs.id}")

            from apps.EasyDocs.accounts.allocations import allocate_payment_shares

            allocations = allocate_payment_shares(
                instance, 
                service_processes=s_processes, 
                sub_services=subs
            )

            if not allocations:
                logger.warning(f"⚠️ No allocations returned for Payment #{instance.id}")
                return

            logger.info(f"📊 Generated {len(allocations)} allocations for Payment #{instance.id}")

            # Apply allocations and create PaymentHistory entries
            for alloc in allocations:
                target = alloc["target"]
                gross = Decimal(alloc["gross"])
                target_type = alloc["target_type"]

                logger.info(
                    f"→ Allocating {gross} KES to {target_type} "
                    f"({getattr(target, 'id', 'N/A')}) — "
                    f"Institution: {alloc.get('institution')} | Company: {alloc.get('company')}"
                )

                if target_type == "service_step":
                    target.paid_amount = (target.paid_amount or Decimal("0.00")) + gross
                    target.save(update_fields=["paid_amount"])
                    PaymentHistory.objects.create(
                        payment=instance,
                        client_service=cs,
                        amount=gross,
                        reason="service_step",
                        service_process=target,
                    )

                elif target_type == "subservice":
                    target.paid_amount = (target.paid_amount or Decimal("0.00")) + gross
                    target.save(update_fields=["paid_amount"])
                    PaymentHistory.objects.create(
                        payment=instance,
                        client_service=cs,
                        amount=gross,
                        reason="sub_service",
                        sub_service=target,
                    )

            logger.info(f"✅ Payment #{instance.id} allocation complete. Total: {instance.amount} KES")

    except Exception as exc:
        logger.exception(f"❌ Allocation error for Payment #{instance.pk}: {exc}")
        raise

# @receiver(post_save, sender=Payment)
# def allocate_payment(sender, instance, created, **kwargs):
#     if not created:
#         return

#     remaining = Decimal(str(instance.amount))
#     client_service = instance.client_service
#     service = client_service.service

#     # 1️⃣ Allocate to service processes (only for TITLE services)
#     if service.category == ServiceCategory.TITLE:
#         for csp in client_service.service_processes.order_by('process__step_order'):
#             if remaining <= 0:
#                 break
#             to_pay = min(remaining, csp.pending_amount)
#             if to_pay > 0:
#                 csp.paid_amount += to_pay
#                 csp.save(update_fields=['paid_amount'])
#                 remaining -= to_pay
#                 PaymentHistory.objects.create(
#                     payment=instance,
#                     client_service=client_service,
#                     amount=to_pay,
#                     reason="service_step",
#                     service_process=csp
#                 )

#     # 2️⃣ Allocate to sub-services (latest ones first)
#     subs = client_service.sub_services.order_by('-id')  # newest first
#     for sub in subs:
#         if remaining <= 0:
#             break
#         to_pay = min(remaining, sub.balance)
#         if to_pay > 0:
#             sub.paid_amount += to_pay
#             sub.save(update_fields=['paid_amount'])
#             remaining -= to_pay
#             PaymentHistory.objects.create(
#                 payment=instance,
#                 client_service=client_service,
#                 amount=to_pay,
#                 reason="sub_service",
#                 sub_service=sub
#             )

#     # 3️⃣ Handle remaining balance for GROUND service
#     if remaining > 0 and service.category == ServiceCategory.GROUND:
#         PaymentHistory.objects.create(
#             payment=instance,
#             client_service=client_service,
#             amount=remaining,
#             reason="ground_service"
#         )
#         # optionally update a paid_amount on ClientService
