# apps/tenant_management/signals.py
import calendar
import logging
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN

from django.db import transaction
from django.db.models.signals import post_save, pre_save, post_delete
from django.dispatch import receiver
from django.utils import timezone

from .models import (
    Lease, Unit, Invoice, InvoiceLine,
    Payment, Receipt, MeterReading, WaterRate, Tenant, Deposit, LedgerEntry, TenantBalance, Deposit
)
from django.db.models import Index, Q, Sum, F
from django.shortcuts import get_object_or_404
from apps.tenant_management.billings.services import get_applicable_rate_for_date, q
from apps.tenant_management.billings.services import apply_credit_and_deposit, allocate_payment_to_deposit_lines
from django.core.exceptions import ObjectDoesNotExist
from django.conf import settings
import threading
from django import db
from django.db import connections
from django.db import DatabaseError


logger = logging.getLogger(__name__)


def month_bounds_for(date_obj):
    """Return (start_of_month, end_of_month) for given date."""
    start = date_obj.replace(day=1)
    last_day = calendar.monthrange(date_obj.year, date_obj.month)[1]
    end = date_obj.replace(day=last_day)
    return start, end


# -------------------------
# Lease & Unit occupancy
# -------------------------
@receiver(post_save, sender=Lease)
def mark_unit_occupied(sender, instance, created, **kwargs):
    """Mark unit occupied when active lease is created."""
    if created and instance.is_active:
        Unit.objects.filter(pk=instance.unit_id).update(is_occupied=True)


@receiver(pre_save, sender=Lease)
def mark_unit_vacant_on_end(sender, instance, **kwargs):
    """Mark unit vacant when lease turned inactive."""
    if not instance.pk:
        return
    old = Lease.objects.get(pk=instance.pk)
    if old.is_active and not instance.is_active:
        Unit.objects.filter(pk=instance.unit_id).update(is_occupied=False)


# -------------------------
# Invoice totals consistency
# -------------------------
@receiver([post_save, post_delete], sender=InvoiceLine)
def update_invoice_total(sender, instance, **kwargs):
    """
    Recalculate invoice total whenever invoice lines change.
    Keeps Invoice.total_amount authoritative.
    """
    invoice = instance.invoice
    total = Decimal('0.00')
    agg = invoice.lines.aggregate(s=Sum('amount'))
    total = agg.get('s') or Decimal('0.00')
    invoice.total_amount = total
    invoice.save(update_fields=['total_amount'])


# -------------------------
# Payments & receipts
# -------------------------




@receiver(post_save, sender=Payment)
def handle_payment_unified(sender, instance, created, **kwargs):
    """
    Handle payment without creating duplicates.
    Deposit allocation is now handled in apply_credit_and_deposit, so we remove it here.
    """
    if not created:
        return

    # REMOVED: Deposit allocation is now handled in apply_credit_and_deposit

    # Update balance_after field
    instance.invoice.refresh_from_db()
    balance_after = instance.invoice.balance
    
    # Use update to avoid triggering another signal
    Payment.objects.filter(pk=instance.pk).update(balance_after=balance_after)

    # Create receipt
    Receipt.objects.create(
        payment=instance,
        receipt_number=f"RCP-{timezone.now().strftime('%Y%m%d%H%M%S')}-{instance.pk}"
    )

    # Mark invoice as paid if balance is zero
    if balance_after <= 0 and not instance.invoice.is_paid:
        instance.invoice.mark_paid()
        logger.info(f"Invoice {instance.invoice.pk} marked as paid after payment {instance.pk}")

# -------------------------
# MeterReading: compute & invoice-line generation (thin signal)
# -------------------------
CENTS = Decimal('0.01')


def _quantize(value: Decimal) -> Decimal:
    """Normalize/round monetary values to 2 dp."""
    if value is None:
        return Decimal('0.00')
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


@receiver(pre_save, sender=MeterReading)
def compute_meter_reading(sender, instance, **kwargs):
    try:
        if not getattr(instance, "unit", None):
            logger.debug("compute_meter_reading: instance.unit not set; skipping")
            return

        # --- Auto-fill previous_reading if omitted ---
        if instance.previous_reading in (None, ''):
            last = (
                MeterReading.objects.filter(unit=instance.unit)
                .exclude(pk=instance.pk)
                .order_by("-reading_date")
                .first()
            )
            if last and last.current_reading is not None:
                instance.previous_reading = last.current_reading
            else:
                instance.previous_reading = Decimal('0.00')

        prev = Decimal(instance.previous_reading or 0)

        # Distinguish "no current reading yet" from zero reading
        if instance.current_reading is None:
            instance.usage = None
            instance.amount = None
            return

        curr = Decimal(instance.current_reading or 0)

        usage = curr - prev
        if usage < 0:
            logger.warning(
                "MeterReading usage negative for unit %s (prev=%s curr=%s); storing 0 as usage",
                getattr(instance, "unit_id", None), prev, curr
            )
            usage = Decimal('0.00')

        instance.usage = _quantize(usage)
        reading_date = getattr(instance, 'reading_date', None) or timezone.now().date()
        rate = get_applicable_rate_for_date(instance.unit.property.water_company, reading_date)
        rate_value = _quantize(getattr(rate, 'rate_per_cubic_meter', Decimal('0.00')))
        instance.rate_per_cubic_meter = rate_value
        instance.amount = _quantize(instance.usage * rate_value)

    except Exception:
        logger.exception("Unexpected error computing MeterReading usage/amount for unit %s",
                         getattr(instance, "unit_id", None))
        instance.usage = instance.usage or None
        instance.amount = instance.amount or None



@receiver(post_save, sender=MeterReading)
def meterreading_post_save(sender, instance, created, **kwargs):
    """
    Enqueue a Celery task after every MeterReading save *only if* the reading
    has a current_reading (i.e. it's ready for processing) AND the unit has an active lease.
    """
    try:
        # Skip if this was already processed in the view
        if hasattr(instance, '_processed_in_view') and instance._processed_in_view:
            logger.debug("meterreading_post_save: reading %s already processed in view; skipping", instance.pk)
            return
            
        if instance.current_reading is None:
            logger.debug("meterreading_post_save: reading %s has no current_reading; not enqueuing", instance.pk)
            return

        # Check if unit has active lease
        active_lease = instance.unit.leases.filter(is_active=True).first()
        if not active_lease:
            logger.debug("meterreading_post_save: reading %s unit has no active lease; not enqueuing", instance.pk)
            return

        # Only enqueue if this is a final reading (has current_reading)
        # and the reading_date is set (should always be true)
        if instance.reading_date:
            from apps.tenant_management.tasks import process_new_meter_reading
            process_new_meter_reading.delay(instance.pk)
            logger.debug("Enqueued meter reading %s for async processing", instance.pk)
    except Exception:
        logger.exception("Failed to enqueue invoice upsert for MeterReading %s", instance.pk)
        
        


@receiver(post_delete, sender=MeterReading)
def remove_water_invoice_line_on_reading_delete(sender, instance, **kwargs):
    """
    When a final reading is deleted and no other readings exist in the billing period,
    remove the associated water invoice line(s).
    """
    try:
        from apps.tenant_management.billings.services import remove_water_invoice_line_for_deleted_reading
        remove_water_invoice_line_for_deleted_reading(instance)
    except Exception:
        logger.exception("Error removing water invoice lines after MeterReading delete %s", getattr(instance, 'pk', None))


# -------------------------
# Deposit lifecycle
# -------------------------
@receiver(post_save, sender=Lease)
def create_deposit_record_for_lease(sender, instance, created, **kwargs):
    """
    On lease creation: create a Deposit record representing the obligation.
    The actual collection happens when the first invoice is paid.
    """
    if not created or not getattr(instance, 'deposit_amount', None):
        return

    existing = Deposit.objects.filter(lease=instance, tenant=instance.tenant).first()
    if existing:
        logger.debug("Deposit already exists for lease %s, skipping.", instance.pk)
        return

    with transaction.atomic():
        deposit = Deposit.objects.create(
            lease=instance,
            tenant=instance.tenant,
            amount=instance.deposit_amount,
            amount_held=Decimal("0.00"),  # Start with 0 held
            paid_at=None,
            notes=f"Deposit obligation created for lease {instance.pk}"
        )

        logger.info("Created deposit obligation %s (amount=%s) for lease %s",
                    deposit.pk, deposit.amount, instance.pk)
        
        # REMOVED: Don't create ledger entry here
        # The ledger entry should be created when the deposit is actually invoiced and paid

# -------------------------
# Payment / Deposit helper (refined)
# -------------------------


# -------------------------
# apply_payment_safe (public API)
# -------------------------
@transaction.atomic
def apply_payment_safe(
    tenant: Tenant,
    payment_amount: Decimal,
    reference: str = None,
    method: str = "Mpesa",
    apply_to_deposit=True,
    invoice: Invoice = None
):
    """
    Public entry for external payments arriving (cash).
    Can optionally target a specific invoice.
    """
    return apply_credit_and_deposit(
        tenant=tenant,
        payment_amount=payment_amount,
        reference=reference,
        method=method,
        apply_to_deposit=apply_to_deposit,
        invoice=invoice
    )


# -------------------------
# Invoice-created receiver (auto-apply existing tenant credit)
# -------------------------
@receiver(post_save, sender=Invoice)
def auto_apply_credit_and_deposit(sender, instance, created, **kwargs):
    """
    When a new invoice is created, consume available TenantBalance credit (if any)
    and attempt to auto-apply it to the newly-created invoice first.
    This path does NOT create deposit top-ups (no external cash).
    """
    if not created:
        return

    with transaction.atomic():
        apply_credit_and_deposit(
            tenant=instance.tenant,
            payment_amount=None,   # indicates "no external cash"
            reference=None,
            method="TenantBalance",
            apply_to_deposit=False,
            invoice=instance
        )


@receiver(pre_save, sender=Lease)
def refund_deposit_on_lease_end(sender, instance, **kwargs):
    """
    When a lease moves from active -> inactive, mark deposit refundable.
    Actual refund must be manually triggered by admin via Deposit.refund().
    """
    if not instance.pk:
        return

    old = Lease.objects.get(pk=instance.pk)
    if old.is_active and not instance.is_active:
        deposits = Deposit.objects.filter(lease=instance, amount_held__gt=Decimal('0.00'))
        for deposit in deposits:
            logger.info(
                "Lease %s ended. Deposit %s is eligible for refund. Admin must trigger refund manually.",
                instance.pk, deposit.pk
            )
