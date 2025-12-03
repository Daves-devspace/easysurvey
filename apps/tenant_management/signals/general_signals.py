import logging
from decimal import Decimal, ROUND_HALF_UP
from django.db.models.signals import post_save, pre_save, post_delete
from django.dispatch import receiver
from django.db.models import Sum
from django.utils import timezone
from ..models import (
    Lease, Unit, Invoice, InvoiceLine, Payment, Receipt, MeterReading, Deposit
)
from apps.tenant_management.utils import get_applicable_rate_for_date
from apps.tenant_management.services.payment_service import PaymentService

logger = logging.getLogger(__name__)
CENTS = Decimal('0.01')

def _quantize(value: Decimal) -> Decimal:
    if value is None: return Decimal('0.00')
    if not isinstance(value, Decimal):
        try: value = Decimal(str(value))
        except: return Decimal('0.00')
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)

# --- CRITICAL FIX: METER READING CALCULATION ---
@receiver(pre_save, sender=MeterReading)
def compute_meter_reading(sender, instance, **kwargs):
    """
    Ensures Usage, Rate, and Amount are calculated BEFORE saving to DB.
    """
    try:
        if not getattr(instance, "unit", None): return

        # 1. Ensure Previous Reading
        if instance.previous_reading is None:
            last = MeterReading.objects.filter(unit=instance.unit).exclude(pk=instance.pk).order_by("-reading_date").first()
            instance.previous_reading = last.current_reading if (last and last.current_reading is not None) else Decimal('0.00')

        # 2. Handle Incomplete Readings (e.g. partial entry)
        if instance.current_reading is None:
            instance.usage = None
            instance.amount = None
            return

        # 3. Calculate Usage
        prev = _quantize(instance.previous_reading)
        curr = _quantize(instance.current_reading)
        usage = curr - prev
        
        # Logic: If usage is negative (meter rollover?), floor at 0 for now
        if usage < 0: 
            usage = Decimal('0.00')
        
        instance.usage = usage

        # 4. Fetch Rate & Calculate Amount
        reading_date = getattr(instance, 'reading_date', None) or timezone.now().date()
        water_company = instance.unit.property.water_company
        
        # DEFAULT to 0.00 if no rate found
        rate_val = Decimal('0.00')
        
        if water_company:
            rate_obj = get_applicable_rate_for_date(water_company, reading_date)
            if rate_obj:
                rate_val = _quantize(rate_obj.rate_per_cubic_meter)
            else:
                logger.warning(f"⚠️ No Active WaterRate found for {water_company.name} on {reading_date}. Billing 0.")
        
        instance.rate_per_cubic_meter = rate_val
        instance.amount = _quantize(usage * rate_val)

    except Exception:
        logger.exception("Error computing MeterReading inside Signal")
        # Safety fallback
        instance.usage = instance.usage or Decimal('0.00')
        instance.amount = instance.amount or Decimal('0.00')

# --- Keep other signals unchanged (Lease, Invoice, etc) ---
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

@receiver(post_save, sender=Payment)
def handle_payment_unified(sender, instance, created, **kwargs):
    if not created: return
    is_allocation = instance.reference and instance.reference.startswith("Allocation from")
    if is_allocation and instance.invoice:
        instance.invoice.refresh_from_db()
        if instance.invoice.balance <= 0 and not instance.invoice.is_paid:
            instance.invoice.mark_paid()
        return 

    try:
        Receipt.objects.create(
            payment=instance,
            receipt_number=f"RCP-{timezone.now().strftime('%Y%m%d%H%M%S')}-{instance.pk}"
        )
        if instance.invoice:
            instance.invoice.refresh_from_db()
            if instance.invoice.balance <= 0 and not instance.invoice.is_paid:
                instance.invoice.mark_paid()
    except Exception as e:
        logger.error(f"Error processing payment {instance.pk}: {e}")

@receiver(post_save, sender=MeterReading)
def meterreading_post_save(sender, instance, created, **kwargs):
    try:
        if hasattr(instance, '_processed_in_view') and instance._processed_in_view: return
        if instance.current_reading is None: return
        if instance.usage is None or instance.usage <= 0: return
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
    Deposit.objects.create(lease=instance, tenant=instance.tenant, amount=instance.deposit_amount, amount_held=Decimal("0.00"), notes=f"Deposit for {instance.pk}")

@receiver(post_save, sender=Invoice)
def auto_apply_credit_and_deposit(sender, instance, created, **kwargs):
    if not created: return
    PaymentService.apply_credit_to_invoice(instance.tenant, instance)

@receiver(pre_save, sender=Lease)
def refund_deposit_on_lease_end(sender, instance, **kwargs):
    pass