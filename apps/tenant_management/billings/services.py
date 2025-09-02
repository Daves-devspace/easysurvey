# apps/tenant_management/billings/services.py
import calendar
from datetime import timedelta, date
from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.utils import timezone
import logging

from apps.tenant_management.models import (
    MeterReading, Invoice, InvoiceLine, WaterRate, Lease, Tenant,
    Deposit, LedgerEntry
)

logger = logging.getLogger(__name__)

CENTS = Decimal('0.01')


def q(amount):
    if amount is None:
        return Decimal('0.00')
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))
    return amount.quantize(CENTS, rounding=ROUND_HALF_UP)


def month_bounds_for(date_obj: date):
    start = date_obj.replace(day=1)
    last_day = calendar.monthrange(date_obj.year, date_obj.month)[1]
    end = date_obj.replace(day=last_day)
    return start, end


def previous_month_bounds(ref_date: date):
    first_of_this_month = ref_date.replace(day=1)
    last_of_prev_month = first_of_this_month - timedelta(days=1)
    start = last_of_prev_month.replace(day=1)
    end = last_of_prev_month.replace(day=calendar.monthrange(last_of_prev_month.year, last_of_prev_month.month)[1])
    return start, end


def normalize_billing_day_for_month(year: int, month: int, billing_day: int) -> int:
    last = calendar.monthrange(year, month)[1]
    return min(billing_day, last)


def billing_period_for_reading_date(reading_date: date, billing_day: int):
    normalized = normalize_billing_day_for_month(reading_date.year, reading_date.month, billing_day)
    if reading_date.day <= normalized:
        return previous_month_bounds(reading_date)
    return month_bounds_for(reading_date)


def get_applicable_rate_for_date(water_company, on_date):
    from ..models import WaterRate
    return (
        WaterRate.objects.filter(water_company=water_company, effective_from__lte=on_date)
        .order_by('-effective_from')
        .first()
    )


def get_active_lease_for_unit(unit):
    return (
        Lease.objects.filter(unit=unit, is_active=True)
        .order_by('-start_date')
        .select_related('tenant', 'unit')
        .first()
    )





@transaction.atomic
def upsert_water_invoice_line_from_reading(reading: MeterReading):
    """
    Upsert the water invoice line for a MeterReading using property's billing_day rules.
    Replaces a placeholder "Water - Pending Reading" line if present, otherwise creates new.
    Returns the InvoiceLine instance or None.
    """
    unit = reading.unit
    if not unit:
        logger.debug("upsert: reading %s has no unit; skipping", getattr(reading, "pk", None))
        return None

    # Guard: only process finalized readings
    if reading.current_reading is None:
        logger.debug("upsert: reading %s has no current_reading; skipping", reading.pk)
        return None

    # Ensure usage exists or compute it deterministically
    usage = getattr(reading, "usage", None)
    if usage is None:
        try:
            prev = Decimal(getattr(reading, "previous_reading", 0) or 0)
            curr = Decimal(getattr(reading, "current_reading", 0) or 0)
            computed = curr - prev
            if computed < 0:
                logger.warning(
                    "upsert: computed negative usage for reading %s (prev=%s curr=%s); treating as 0",
                    reading.pk, prev, curr
                )
                computed = Decimal("0.00")
            usage = q(computed)
        except Exception:
            logger.exception("upsert: failed to compute usage for reading %s; skipping", reading.pk)
            return None
    else:
        usage = q(usage)

    # Compute amount if not already present
    amount = getattr(reading, "amount", None)
    if amount is None:
        rate = get_applicable_rate_for_date(unit.property.water_company, reading.reading_date)
        rate_value = Decimal(getattr(rate, "rate_per_cubic_meter", 0)) if rate else Decimal("0.00")
        amount = q(usage * rate_value)
    else:
        amount = q(amount)

    # Billing period and latest-check logic
    billing_day = getattr(unit.property, "billing_day", None) or 1
    billing_start, billing_end = billing_period_for_reading_date(reading.reading_date, billing_day)
    normalized = normalize_billing_day_for_month(reading.reading_date.year, reading.reading_date.month, billing_day)
    latest_cutoff = reading.reading_date if reading.reading_date.day <= normalized else billing_end

    latest = (
        MeterReading.objects
        .filter(unit=unit, reading_date__gte=billing_start, reading_date__lte=latest_cutoff)
        .order_by('-reading_date')
        .first()
    )

    if not latest or latest.pk != reading.pk:
        logger.debug(
            "upsert: reading %s is not latest for unit %s in window %s-%s; skipping",
            getattr(reading, "pk", None), unit.pk, billing_start, latest_cutoff
        )
        return None

    lease = get_active_lease_for_unit(unit)
    if not lease:
        logger.debug("upsert: no active lease for unit %s; skipping reading %s", unit.pk, getattr(reading, "pk", None))
        return None

    # Lock tenant row (you had this previously) if you need tenant-level consistency
    tenant = Tenant.objects.select_for_update().get(pk=lease.tenant.pk)

    # Get or create invoice, then lock it for update to avoid race when mutating lines
    invoice, created = Invoice.objects.get_or_create(
        tenant=tenant,
        billing_period_start=billing_start,
        billing_period_end=billing_end,
        defaults={'total_amount': q(Decimal('0.00'))}
    )

    # Lock the invoice row now that we have its PK so concurrent workers wait
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)

    description = f"Water Usage - Unit {unit.unit_number} ({billing_start.isoformat()} to {billing_end.isoformat()})"

    # Try to find a placeholder (water line with no meter_reading) for the same lease
    placeholder = invoice.lines.filter(
        lease=lease,
        line_type=InvoiceLine.LINE_WATER,
        meter_reading__isnull=True
    ).first()

    if placeholder:
        placeholder.meter_reading = reading
        placeholder.description = description
        placeholder.amount = amount
        placeholder.save(update_fields=["meter_reading", "description", "amount"])
        il = placeholder
        logger.info("upsert: updated placeholder water line %s with reading %s", il.pk, reading.pk)
    else:
        # Remove any existing water line for this lease that references prior meter_reading
        # (we keep this conservative: only delete for same invoice & lease)
        invoice.lines.filter(line_type=InvoiceLine.LINE_WATER, lease=lease).delete()

        il = InvoiceLine.objects.create(
            invoice=invoice,
            lease=lease,
            meter_reading=reading,
            line_type=InvoiceLine.LINE_WATER,
            description=description,
            amount=amount,
        )
        logger.info("upsert: created new water line %s for reading %s", il.pk, reading.pk)

    # Update invoice status and totals (invoice locked via select_for_update)
    has_rent = invoice.lines.filter(line_type=InvoiceLine.LINE_RENT).exists()
    has_water = invoice.lines.filter(line_type=InvoiceLine.LINE_WATER, meter_reading__isnull=False).exists()

    if has_rent and has_water:
        new_status = Invoice.STATUS_FINALIZED
    elif has_rent and not has_water:
        new_status = Invoice.STATUS_PENDING
    elif has_water and not has_rent:
        new_status = Invoice.STATUS_PENDING
    else:
        new_status = Invoice.STATUS_DRAFT

    if invoice.status != new_status:
        invoice.status = new_status
        invoice.save(update_fields=['status'])

    # Recompute invoice totals deterministically
    invoice.recalc_total()

    return il




@transaction.atomic
def upsert_rent_invoice_line_for_lease(lease: Lease, billing_date=None):
    """
    Ensure rent line exists in invoice for this lease & billing period.
    Also ensure a placeholder water line exists if meter reading is missing.
    """
    if not lease.is_active:
        return None

    billing_date = billing_date or timezone.now().date()
    billing_day = getattr(lease.unit.property, "billing_day", None) or 1
    billing_start, billing_end = billing_period_for_reading_date(billing_date, billing_day)

    tenant = Tenant.objects.select_for_update().get(pk=lease.tenant.pk)

    invoice, created = Invoice.objects.get_or_create(
        tenant=tenant,
        billing_period_start=billing_start,
        billing_period_end=billing_end,
        defaults={"total_amount": Decimal("0.00")},
    )

    # --- Rent line ---
    description = f"Rent - Unit {lease.unit.unit_number} ({billing_start.isoformat()} to {billing_end.isoformat()})"
    invoice.lines.filter(
        invoice=invoice,
        lease=lease,
        line_type=InvoiceLine.LINE_RENT
    ).delete()

    InvoiceLine.objects.create(
        invoice=invoice,
        lease=lease,
        line_type=InvoiceLine.LINE_RENT,
        description=description,
        amount=q(lease.unit.rent_amount or Decimal("0.00")),
    )

    # --- Water placeholder ---
    has_water_line = invoice.lines.filter(line_type=InvoiceLine.LINE_WATER, lease=lease).exists()
    if not has_water_line:
        InvoiceLine.objects.create(
            invoice=invoice,
            lease=lease,
            line_type=InvoiceLine.LINE_WATER,
            description=f"Water - Pending Reading (Unit {lease.unit.unit_number})",
            amount=Decimal("0.00"),
        )
        logger.info("💧 Created placeholder water line for lease=%s invoice=%s", lease.pk, invoice.pk)

    # --- Invoice status ---
    has_real_water = invoice.lines.filter(
        line_type=InvoiceLine.LINE_WATER,
        meter_reading__isnull=False
    ).exists()

    invoice.status = Invoice.STATUS_FINALIZED if has_real_water else Invoice.STATUS_PENDING
    invoice.save(update_fields=["status"])

    # 🔑 Explicit total recompute
    invoice.recalc_total()

    return invoice







@transaction.atomic
def apply_deposit_to_invoice(deposit: Deposit, invoice: Invoice, lease: Lease = None, amount: Decimal | None = None):
    """
    Apply part or all of deposit.amount_held to the invoice.
    - amount: optional requested amount; if None, apply up to min(amount_held, invoice.balance)
    - Create LedgerEntry (credit), reduce Deposit.amount_held, and create a negative InvoiceLine
    - Return the LedgerEntry or None if nothing applied
    """
    # compute amount to apply
    deposit.refresh_from_db()  # ensure latest amount_held
    amount_held = q(deposit.amount_held)
    invoice_balance = q(invoice.total_amount - invoice.total_paid)  # uses properties
    if amount is None:
        apply_amount = min(amount_held, invoice_balance)
    else:
        apply_amount = min(q(amount), amount_held, invoice_balance)

    apply_amount = q(apply_amount)
    if apply_amount <= Decimal('0.00'):
        return None

    # ledger entry (credit reduces liability)
    le = LedgerEntry.objects.create(
        lease=lease or deposit.lease,
        tenant=deposit.tenant,
        invoice=invoice,
        deposit=deposit,
        debit=Decimal('0.00'),
        credit=apply_amount,
        entry_type=LedgerEntry.DEPOSIT,
        description=f"Deposit applied to Invoice #{invoice.id} (Deposit #{deposit.pk})"
    )

    # reduce deposit.amount_held
    deposit.amount_held = q(deposit.amount_held - apply_amount)
    deposit.save(update_fields=['amount_held'])

    # negative invoice line to represent deposit usage
    InvoiceLine.objects.create(
        invoice=invoice,
        lease=lease or deposit.lease,
        meter_reading=None,
        description=f"Deposit applied (Deposit #{deposit.pk})",
        amount=q(-apply_amount),
    )

    return le


@transaction.atomic
def refund_deposit(deposit: Deposit, amount: Decimal | None = None):
    """
    Refund part or all of deposit.amount_held.
    - reduces amount_held and records refunded_amount, creates a LedgerEntry (credit), and returns deposit
    - actual cash refund Payment creation should be performed by caller if needed
    """
    deposit.refresh_from_db()
    amount_held = q(deposit.amount_held)
    if amount is None:
        refund_amount = amount_held
    else:
        refund_amount = min(q(amount), amount_held)

    refund_amount = q(refund_amount)
    if refund_amount <= Decimal('0.00'):
        return None

    deposit.refunded_amount = q((deposit.refunded_amount or Decimal('0.00')) + refund_amount)
    deposit.amount_held = q(deposit.amount_held - refund_amount)
    deposit.refunded_at = timezone.now()
    deposit.save(update_fields=['refunded_amount', 'amount_held', 'refunded_at'])

    LedgerEntry.objects.create(
        lease=deposit.lease,
        tenant=deposit.tenant,
        deposit=deposit,
        debit=Decimal('0.00'),
        credit=refund_amount,
        entry_type=LedgerEntry.DEPOSIT,
        description=f"Deposit refunded (Deposit #{deposit.pk})"
    )

    return deposit
