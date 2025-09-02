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
def handle_payment(sender, instance, created, **kwargs):
    """
    On new payment: update balance_after, create receipt, mark invoice paid if cleared.
    """
    if not created:
        return

    balance_after = instance.invoice.balance
    Payment.objects.filter(pk=instance.pk).update(balance_after=balance_after)

    Receipt.objects.create(
        payment=instance,
        receipt_number=f"RCP-{timezone.now().strftime('%Y%m%d%H%M%S')}-{instance.pk}"
    )

    if instance.invoice.balance <= 0 and not instance.invoice.is_paid:
        instance.invoice.mark_paid()


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
    has a current_reading (i.e. it's ready for processing).
    """
    try:
        if instance.current_reading is None:
            logger.debug("meterreading_post_save: reading %s has no current_reading; not enqueuing", instance.pk)
            return

        from apps.tenant_management.tasks import process_new_meter_reading
        process_new_meter_reading.delay(instance.pk)
        logger.debug("📤 Enqueued meter reading %s for async processing", instance.pk)
    except Exception:
        logger.exception("❌ Failed to enqueue invoice upsert for MeterReading %s", instance.pk)


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
            amount_held=Decimal("0.00"),
            paid_at=None
        )

        logger.info("Created deposit obligation %s (amount=%s) for lease %s",
                    deposit.pk, deposit.amount, instance.pk)


# -------------------------
# Payment / Deposit helper (refined)
# -------------------------
def _apply_credit_and_deposit(
    tenant,
    payment_amount: Decimal = None,
    reference: str = None,
    method: str = "Mpesa",
    apply_to_deposit: bool = True,
    invoice=None,
    use_logger: bool = True  # optional flag to switch between print/log
):
    """
    Apply payment or tenant credits to invoices and optionally deposit.

    - If payment_amount is None, uses unallocated tenant credits.
    - Applies to unpaid invoices in order, then optionally to deposit top-up.
    - Any leftover real payment becomes tenant credit.
    """

    def _quantize(value):
        return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

    def log(msg):
        if use_logger:
            logger.debug(msg)
        else:
            print(msg)

    def to_dec(v):
        return _quantize(v) if v is not None else None

    payment_left = to_dec(payment_amount)
    applied_to_invoices = Decimal("0.00")
    applied_to_deposit = Decimal("0.00")

    tenant_balance_obj, _ = TenantBalance.objects.get_or_create(tenant=tenant)
    active_lease = Lease.objects.filter(tenant=tenant, is_active=True).first()

    # Determine available credit
    if payment_left is None:
        unallocated_credit_entries = LedgerEntry.objects.filter(
            tenant=tenant,
            invoice__isnull=True,
            deposit__isnull=True,
            credit__gt=0
        )
        available_credit = sum(_quantize(e.credit) for e in unallocated_credit_entries)
        payment_source = "TenantBalance"
        using_tenant_credit = True
        log(f"[DEBUG] Using tenant credit: total available={available_credit}")
    else:
        available_credit = payment_left
        payment_source = method
        using_tenant_credit = False
        log(f"[DEBUG] Using real payment: amount={payment_left}, method={method}")

    # Determine unpaid invoices
    if invoice:
        unpaid_qs = list(Invoice.objects.filter(pk=invoice.pk, tenant=tenant, is_paid=False))
        unpaid_qs += list(
            Invoice.objects.filter(tenant=tenant, is_paid=False)
            .exclude(pk=invoice.pk)
            .order_by('billing_period_start', 'id')
        )
    else:
        unpaid_qs = Invoice.objects.filter(tenant=tenant, is_paid=False).order_by('billing_period_start', 'id')

    log(f"[DEBUG] Found {len(unpaid_qs)} unpaid invoices for tenant {tenant}")

    # Apply to invoices
    for inv in unpaid_qs:
        if available_credit <= 0:
            break

        allocate = _quantize(min(available_credit, inv.balance))
        if allocate <= 0:
            continue

        Payment.objects.create(
            invoice=inv,
            amount=allocate,
            method=payment_source,
            reference=(reference if payment_amount is not None else "Auto-applied tenant credit")
        )
        log(f"[DEBUG] Allocated {allocate} to Invoice {inv.pk}")

        if using_tenant_credit:
            remaining = allocate
            for entry in unallocated_credit_entries:
                if remaining <= 0:
                    break
                to_use = min(_quantize(entry.credit), remaining)

                if to_use < entry.credit:
                    entry.credit -= to_use
                    entry.save(update_fields=["credit"])
                    LedgerEntry.objects.create(
                        lease=entry.lease,
                        tenant=entry.tenant,
                        invoice=inv,
                        deposit=None,
                        credit=to_use,
                        debit=Decimal("0.00"),
                        entry_type=entry.entry_type,
                        description=f"Applied credit to Invoice {inv.pk}"
                    )
                    log(f"[DEBUG] Split tenant credit entry: used {to_use}, remaining {entry.credit}")
                else:
                    entry.invoice = inv
                    entry.save(update_fields=["invoice"])
                    log(f"[DEBUG] Applied full tenant credit entry {entry.pk} to Invoice {inv.pk}")

                remaining -= to_use

        applied_to_invoices += allocate
        inv.recalc_total()
        if inv.balance <= 0 and not inv.is_paid:
            inv.mark_paid()
            log(f"[DEBUG] Invoice {inv.pk} marked as paid")

        available_credit -= allocate
        if not using_tenant_credit:
            payment_left -= allocate

    # Deposit top-up (real payments only)
    if apply_to_deposit and active_lease and payment_left is not None and payment_left > 0:
        deposit = Deposit.objects.filter(tenant=tenant).order_by('-lease__is_active', '-id').first()
        if deposit:
            shortfall = deposit.amount - deposit.amount_held
            if shortfall > 0:
                allocate = _quantize(min(payment_left, shortfall))
                LedgerEntry.objects.create(
                    lease=deposit.lease,
                    tenant=tenant,
                    deposit=deposit,
                    debit=Decimal("0.00"),
                    credit=allocate,
                    entry_type=LedgerEntry.DEPOSIT,
                    description=f"Top-up deposit for Lease {deposit.lease.pk}"
                )
                deposit.amount_held += allocate
                deposit.paid_at = deposit.paid_at or timezone.now()
                deposit.save(update_fields=["amount_held", "paid_at"])
                applied_to_deposit += allocate
                payment_left -= allocate
                log(f"[DEBUG] Applied {allocate} to deposit for Lease {deposit.lease.pk}")

    # Overpayment stored as tenant credit (real payments)
    if not using_tenant_credit and payment_left is not None and payment_left > 0:
        LedgerEntry.objects.create(
            lease=None,
            tenant=tenant,
            invoice=None,
            deposit=None,
            debit=Decimal("0.00"),
            credit=payment_left,
            entry_type=LedgerEntry.RENT,
            description="Overpayment stored as tenant credit"
        )
        log(f"[DEBUG] Stored overpayment of {payment_left} as tenant credit")
        payment_left = Decimal("0.00")

    # Recalculate tenant balance using robust method
    TenantBalance.recalc_for_tenant(tenant)
    log(f"[DEBUG] Tenant balance recalculated for {tenant}")

    return {
        "applied_to_deposit": str(_quantize(applied_to_deposit)),
        "applied_to_invoices": str(_quantize(applied_to_invoices)),
        "unallocated": str(_quantize(payment_left)) if payment_left is not None else None,
    }









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
    return _apply_credit_and_deposit(
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
        _apply_credit_and_deposit(
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
