# apps/tenant_management/signals.py
import calendar
import logging
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN

from django.db import transaction
from django.db.models.signals import post_save, pre_save, post_delete
from django.dispatch import receiver
from django.utils import timezone

from ..models import (
    Lease, Unit, Invoice, InvoiceLine,
    Payment, Receipt, MeterReading, WaterRate, Tenant, Deposit, LedgerEntry, TenantBalance, Deposit
)
from django.db.models import Index, Q, Sum, F
from django.shortcuts import get_object_or_404

from django import db
from django.db import connections
from django.db import DatabaseError
from apps.tenant_management.services.payment_service import PaymentService
from apps.tenant_management.utils import get_applicable_rate_for_date


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
    if created and instance.is_active:
        Unit.objects.filter(pk=instance.unit_id).update(is_occupied=True)

@receiver(pre_save, sender=Lease)
def mark_unit_vacant_on_end(sender, instance, **kwargs):
    if not instance.pk: return
    old = Lease.objects.get(pk=instance.pk)
    if old.is_active and not instance.is_active:
        Unit.objects.filter(pk=instance.unit_id).update(is_occupied=False)

@receiver([post_save, post_delete], sender=InvoiceLine)
def update_invoice_total(sender, instance, **kwargs):
    invoice = instance.invoice
    agg = invoice.lines.aggregate(s=Sum('amount'))
    invoice.total_amount = agg.get('s') or Decimal('0.00')
    invoice.save(update_fields=['total_amount'])

# -------------------------
# Payments & receipts (FIXED)
# -------------------------
@receiver(post_save, sender=Payment)
def handle_payment_unified(sender, instance, created, **kwargs):
    """
    Handle payment creation.
    FIX: Only generate receipts for REAL payments (MIXED/CREDIT), not internal allocations.
    """
    if not created:
        return

    # 1. Skip Receipt generation for internal allocations
    # Allocations are just moving money from Master -> Invoice. We don't need a receipt for that.
    is_allocation = instance.reference and instance.reference.startswith("Allocation from")
    
    # If it's an allocation, we JUST check if the invoice is paid, then exit.
    if is_allocation and instance.invoice:
        instance.invoice.refresh_from_db()
        if instance.invoice.balance <= 0 and not instance.invoice.is_paid:
            instance.invoice.mark_paid()
            logger.info(f"Invoice {instance.invoice.pk} marked as paid via allocation {instance.pk}")
        return 

    # 2. Generate Receipt for Master Payments Only
    try:
        Receipt.objects.create(
            payment=instance,
            receipt_number=f"RCP-{timezone.now().strftime('%Y%m%d%H%M%S')}-{instance.pk}"
        )
        logger.info(f"Created receipt for payment {instance.pk}")

        # If this master payment was directly linked to an invoice (Single Invoice Strategy), check status
        if instance.invoice:
            instance.invoice.refresh_from_db()
            if instance.invoice.balance <= 0 and not instance.invoice.is_paid:
                instance.invoice.mark_paid()

    except Exception as e:
        logger.error(f"Error processing payment {instance.pk}: {e}")

# -------------------------
# MeterReading Signals (Keep existing logic)
# -------------------------
CENTS = Decimal('0.01')

def _quantize(value: Decimal) -> Decimal:
    if value is None: return Decimal('0.00')
    if not isinstance(value, Decimal): value = Decimal(str(value))
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)

@receiver(pre_save, sender=MeterReading)
def compute_meter_reading(sender, instance, **kwargs):
    try:
        if not getattr(instance, "unit", None): return

        if instance.previous_reading in (None, ''):
            last = MeterReading.objects.filter(unit=instance.unit).exclude(pk=instance.pk).order_by("-reading_date").first()
            instance.previous_reading = last.current_reading if (last and last.current_reading is not None) else Decimal('0.00')

        if instance.current_reading is None:
            instance.usage = None; instance.amount = None
            return

        prev = Decimal(instance.previous_reading or 0)
        curr = Decimal(instance.current_reading or 0)
        usage = curr - prev
        if usage < 0: usage = Decimal('0.00')

        instance.usage = _quantize(usage)
        reading_date = getattr(instance, 'reading_date', None) or timezone.now().date()
        rate = get_applicable_rate_for_date(instance.unit.property.water_company, reading_date)
        rate_value = _quantize(getattr(rate, 'rate_per_cubic_meter', Decimal('0.00')))
        instance.rate_per_cubic_meter = rate_value
        instance.amount = _quantize(instance.usage * rate_value)
    except Exception:
        logger.exception("Error computing MeterReading")

@receiver(post_save, sender=MeterReading)
def meterreading_post_save(sender, instance, created, **kwargs):
    try:
        if hasattr(instance, '_processed_in_view') and instance._processed_in_view: return
        if instance.current_reading is None: return
        if not instance.unit.leases.filter(is_active=True).exists(): return

        if instance.reading_date:
            from apps.tenant_management.tasks import process_new_meter_reading
            process_new_meter_reading.delay(instance.pk)
    except Exception:
        logger.exception("Failed to enqueue invoice upsert")

@receiver(post_delete, sender=MeterReading)
def remove_water_invoice_line_on_reading_delete(sender, instance, **kwargs):
    try:
        from apps.tenant_management.billing.services import remove_water_invoice_line_for_deleted_reading
        remove_water_invoice_line_for_deleted_reading(instance)
    except Exception: pass

@receiver(post_save, sender=Lease)
def create_deposit_record_for_lease(sender, instance, created, **kwargs):
    if not created or not getattr(instance, 'deposit_amount', None): return
    if Deposit.objects.filter(lease=instance, tenant=instance.tenant).exists(): return

    Deposit.objects.create(
        lease=instance, tenant=instance.tenant,
        amount=instance.deposit_amount, amount_held=Decimal("0.00"),
        notes=f"Deposit obligation created for lease {instance.pk}"
    )

# -------------------------
# Auto-Apply Credit
# -------------------------
@receiver(post_save, sender=Invoice)
def auto_apply_credit_and_deposit(sender, instance, created, **kwargs):
    if not created: return
    PaymentService.apply_credit_to_invoice(instance.tenant, instance)

@receiver(pre_save, sender=Lease)
def refund_deposit_on_lease_end(sender, instance, **kwargs):
    if not instance.pk: return
    old = Lease.objects.get(pk=instance.pk)
    if old.is_active and not instance.is_active:
        # Just logging, manual refund required
        pass