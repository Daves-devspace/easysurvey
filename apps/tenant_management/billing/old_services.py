# billings/services.py
import calendar
from datetime import timedelta, date
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN
from django.db import transaction
from django.utils import timezone
import logging

from apps.tenant_management.models import (
    MeterReading, Invoice, InvoiceLine, WaterRate, Lease, Tenant,TenantBalance, Payment,
    Deposit, LedgerEntry
)
from functools import lru_cache
from django.db.models import Q
from datetime import date as _date
from django.db.models import Sum
# keep imports for models
from ..models import WaterRate

from django.db import IntegrityError






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
    """
    Ensures billing_day does not exceed the last day of the month.
    """
    last_day = calendar.monthrange(year, month)[1]
    return min(billing_day, last_day)



def billing_period_for_billing_month(billing_month_date: date, billing_day: int):
    """
    Given a date that represents a billing-month (e.g. 2025-08-01) and a billing_day,
    return (start_date, end_date) where:
      start = billing_month_date's year/month at billing_day (normalized)
      end   = next month at billing_day (normalized)
    Example: billing_month_date = 2025-08-01, billing_day = 5
             -> (2025-08-05, 2025-09-05)
    """
    year = billing_month_date.year
    month = billing_month_date.month

    start_day = normalize_billing_day_for_month(year, month, billing_day)
    start_date = date(year, month, start_day)

    # next month
    if month == 12:
        next_month = 1
        next_year = year + 1
    else:
        next_month = month + 1
        next_year = year

    end_day = normalize_billing_day_for_month(next_year, next_month, billing_day)
    end_date = date(next_year, next_month, end_day)

    return start_date, end_date



def billing_period_for_reading_date(reading_date: date, billing_day: int):
    """
    Returns a tuple (start_date, end_date) representing the billing period
    for a given reading_date and property's billing_day.
    
    Logic:
    - If reading_date < billing_day → belongs to previous period
    - If reading_date >= billing_day → belongs to current period
    - Period always spans from billing_day of start month → billing_day of end month
    """
    # normalize current month billing day
    current_month_billing_day = normalize_billing_day_for_month(
        reading_date.year, reading_date.month, billing_day
    )

    if reading_date.day < current_month_billing_day:
        # belongs to previous period
        # calculate previous month
        if reading_date.month == 1:
            prev_month = 12
            year = reading_date.year - 1
        else:
            prev_month = reading_date.month - 1
            year = reading_date.year

        start_day = normalize_billing_day_for_month(year, prev_month, billing_day)
        start_date = date(year, prev_month, start_day)

        # end date is current period's billing day
        end_date = date(reading_date.year, reading_date.month, current_month_billing_day)
    else:
        # belongs to current period
        start_date = date(reading_date.year, reading_date.month, current_month_billing_day)
        # next month
        if reading_date.month == 12:
            next_month = 1
            year = reading_date.year + 1
        else:
            next_month = reading_date.month + 1
            year = reading_date.year
        end_day = normalize_billing_day_for_month(year, next_month, billing_day)
        end_date = date(year, next_month, end_day)

    return start_date, end_date


def _date_key(d):
    if d is None:
        return "none"
    if isinstance(d, _date):
        return d.isoformat()
    return str(d)


@lru_cache(maxsize=1024)
def _cached_rate_lookup(water_company_id, on_date_iso):
    """
    Internal cached lookup. Returns WaterRate.id or None.
    We return the id so the top-level function can fetch the object (or you can return attributes).
    """
    try:
        on_date = None if on_date_iso == "none" else _date.fromisoformat(on_date_iso)
    except Exception:
        on_date = None

    qs = WaterRate.objects.filter(water_company_id=water_company_id)
    if on_date:
        qs = qs.filter(effective_from__lte=on_date).filter(
            Q(effective_to__gte=on_date) | Q(effective_to__isnull=True)
        ).order_by('-effective_from')
    else:
        qs = qs.order_by('-effective_from')

    rate = qs.first()
    return rate.pk if rate else None

def get_applicable_rate_for_date(water_company, on_date):
    """
    Public API: returns WaterRate instance or None.
    Uses small LRU cache to avoid repeated DB hits in the same process.
    """
    if water_company is None:
        return None

    key = (int(water_company.pk), _date_key(on_date))
    rate_id = _cached_rate_lookup(key[0], key[1])
    if rate_id:
        # fetch the object (should be cached in ORM layer if recently queried),
        # or return a minimal object: here we fetch
        return WaterRate.objects.get(pk=rate_id)
    return None

def get_active_lease_for_unit(unit):
    return (
        Lease.objects.filter(unit=unit, is_active=True)
        .order_by('-start_date')
        .select_related('tenant', 'unit')
        .first()
    )




# @transaction.atomic
# def get_or_create_monthly_invoice(tenant, billing_date: date):
#     """
#     Get or create invoice for the tenant respecting the property's billing_day.
#     Billing_day determines whether current date belongs to previous month or current month.
#     Fully synced with billing_period_for_reading_date logic.
#     """
#     billing_day = tenant.property.billing_day
#     start, end = billing_period_for_reading_date(billing_date, billing_day)

#     while True:
#         try:
#             # Lock row to prevent race condition
#             invoice = (
#                 Invoice.objects.select_for_update()
#                 .filter(tenant=tenant, billing_period_start=start, billing_period_end=end)
#                 .first()
#             )
#             if invoice:
#                 return invoice

#             return Invoice.objects.create(
#                 tenant=tenant,
#                 billing_period_start=start,
#                 billing_period_end=end,
#                 status=Invoice.STATUS_DRAFT,
#             )
#         except IntegrityError:
#             continue  # retry in case of race condition
@transaction.atomic
def get_or_create_monthly_invoice(tenant, billing_date: date):
    """
    Get or create invoice for the tenant respecting the property's billing_day.
    Billing_day determines whether current date belongs to previous month or current month.
    Fully synced with billing_period_for_reading_date logic.
    """
    billing_day = tenant.property.billing_day
    start, end = billing_period_for_reading_date(billing_date, billing_day)

    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            # Lock row to prevent race condition
            invoice = (
                Invoice.objects.select_for_update()
                .filter(tenant=tenant, billing_period_start=start, billing_period_end=end)
                .first()
            )
            if invoice:
                return invoice

            # Try to create new invoice
            invoice = Invoice.objects.create(
                tenant=tenant,
                billing_period_start=start,
                billing_period_end=end,
                status=Invoice.STATUS_DRAFT,
            )
            return invoice
            
        except IntegrityError:
            retry_count += 1
            if retry_count >= max_retries:
                # If we still can't create after retries, try one more select
                invoice = (
                    Invoice.objects.select_for_update()
                    .filter(tenant=tenant, billing_period_start=start, billing_period_end=end)
                    .first()
                )
                if invoice:
                    return invoice
                # If still no invoice found, re-raise the error
                raise
            continue


@transaction.atomic
def get_or_create_invoice_for_period(tenant, start_date: date, end_date: date):
    """
    Get or create invoice for a specific billing period.
    Used when we know the exact period dates.
    """
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            # Lock row to prevent race condition
            invoice = (
                Invoice.objects.select_for_update()
                .filter(tenant=tenant, billing_period_start=start_date, billing_period_end=end_date)
                .first()
            )
            if invoice:
                return invoice

            invoice = Invoice.objects.create(
                tenant=tenant,
                billing_period_start=start_date,
                billing_period_end=end_date,
                status=Invoice.STATUS_DRAFT,
            )
            return invoice
            
        except IntegrityError:
            retry_count += 1
            if retry_count >= max_retries:
                invoice = (
                    Invoice.objects.select_for_update()
                    .filter(tenant=tenant, billing_period_start=start_date, billing_period_end=end_date)
                    .first()
                )
                if invoice:
                    return invoice
                raise
            continue



@transaction.atomic
def upsert_rent_invoice_line_for_lease(lease: Lease, billing_date: date = None, is_first_invoice: bool = False):
    """
    Create or update rent & deposit lines for the lease respecting billing_day.
    - Rent line is always created for the billing period of the billing_date.
    - Deposit line only created for first invoice when explicitly indicated.
    """
    billing_date = billing_date or date.today()
    invoice = get_or_create_monthly_invoice(lease.tenant, billing_date)

    # --- Rent Line ---
    rent_line, created = InvoiceLine.objects.get_or_create(
        invoice=invoice,
        lease=lease,
        line_type=InvoiceLine.LINE_RENT,
        defaults={
            "description": f"Monthly Rent ({invoice.billing_period_start:%b %Y})",
            "amount": q(lease.unit.rent_amount),
        }
    )

    if not created and rent_line.amount != lease.unit.rent_amount:
        rent_line.amount = q(lease.unit.rent_amount)
        rent_line.save(update_fields=["amount"])

    # --- Deposit Line (first invoice only when explicitly indicated) ---
    # Check if this is the first invoice for this specific lease
    # by looking for any existing deposit lines for this lease
    has_existing_deposit = InvoiceLine.objects.filter(
        lease=lease,
        line_type=InvoiceLine.LINE_DEPOSIT
    ).exists()
    
    is_first_invoice_for_lease = not has_existing_deposit

    if is_first_invoice_for_lease and lease.deposit_amount > 0:
        deposit, _ = Deposit.objects.get_or_create(
            lease=lease,
            tenant=lease.tenant,
            defaults={"amount": lease.deposit_amount, "amount_held": Decimal('0.00')}
        )
        
        # Create the deposit line but DON'T create a ledger entry here
        InvoiceLine.objects.get_or_create(
            invoice=invoice,
            lease=lease,
            line_type=InvoiceLine.LINE_DEPOSIT,
            deposit=deposit,
            defaults={
                "description": f"Security Deposit ({lease.unit.unit_number})",
                "amount": q(lease.deposit_amount),
            }
        )

    invoice.recalc_total()
    return invoice

# @transaction.atomic
# def upsert_rent_invoice_line_for_lease(lease: Lease, billing_date: date = None):
#     """
#     Create or update rent & deposit lines for the lease respecting billing_day.
#     - Rent line is always created for the billing period of the billing_date.
#     - Deposit line only created for first invoice.
#     """
#     billing_date = billing_date or date.today()
#     invoice = get_or_create_monthly_invoice(lease.tenant, billing_date)

#     # --- Rent Line ---
#     rent_line, created = InvoiceLine.objects.get_or_create(
#         invoice=invoice,
#         lease=lease,
#         line_type=InvoiceLine.LINE_RENT,
#         defaults={
#             "description": f"Monthly Rent ({invoice.billing_period_start:%b %Y})",
#             "amount": q(lease.unit.rent_amount),
#         }
#     )

#     if not created and rent_line.amount != lease.unit.rent_amount:
#         rent_line.amount = q(lease.unit.rent_amount)
#         rent_line.save(update_fields=["amount"])

#     # --- Deposit Line (first invoice only) ---
#     is_first_invoice = not Invoice.objects.filter(
#         tenant=lease.tenant,
#         billing_period_start__lt=invoice.billing_period_start
#     ).exists()

#     if is_first_invoice and lease.deposit_amount > 0:
#         deposit, _ = Deposit.objects.get_or_create(
#             lease=lease,
#             tenant=lease.tenant,
#             defaults={"amount": lease.deposit_amount, "amount_held": Decimal('0.00')}
#         )
        
#         # Create the deposit line but DON'T create a ledger entry here
#         InvoiceLine.objects.get_or_create(
#             invoice=invoice,
#             lease=lease,
#             line_type=InvoiceLine.LINE_DEPOSIT,
#             deposit=deposit,
#             defaults={
#                 "description": f"Security Deposit ({lease.unit.unit_number})",
#                 "amount": q(lease.deposit_amount),
#             }
#         )
        
#         # REMOVED: Don't create ledger entry here
#         # The ledger entry should be created only when the deposit is actually paid

#     invoice.recalc_total()
#     return invoice




@transaction.atomic
def upsert_water_invoice_line_from_reading(reading, billing_month_date=None):
    """
    Create or update water usage line for a unit's lease.
    If billing_month_date is provided, use it to determine the billing period.
    Otherwise fall back to reading.reading_date.
    """
    lease = Lease.objects.filter(unit=reading.unit, is_active=True).select_related('tenant').first()
    if not lease:
        return None

    # Use the user-selected billing month if provided
    if billing_month_date:
        billing_day = lease.tenant.property.billing_day
        start, end = billing_period_for_billing_month(billing_month_date, billing_day)
        invoice = get_or_create_invoice_for_period(lease.tenant, start, end)
    else:
        # Fallback to reading date logic
        billing_date = reading.reading_date
        invoice = get_or_create_monthly_invoice(lease.tenant, billing_date)

    amount = q((reading.usage or 0) * (reading.rate_per_cubic_meter or 0))

    line, created = InvoiceLine.objects.get_or_create(
        invoice=invoice,
        lease=lease,
        line_type=InvoiceLine.LINE_WATER,
        meter_reading=reading,
        defaults={
            "description": f"Water usage ({invoice.billing_period_start:%b %Y})",
            "amount": amount,
        }
    )

    if not created and line.amount != amount:
        line.amount = amount
        line.save(update_fields=["amount"])

    invoice.recalc_total()
    return line


@transaction.atomic
def allocate_payment_to_deposit_lines(invoice, payment_amount, payment_record):
    """
    SIMPLIFIED: This function is now mainly called for backward compatibility.
    The main logic is handled within apply_credit_and_deposit.
    """
    deposit_lines = invoice.lines.filter(line_type=InvoiceLine.LINE_DEPOSIT)
    if not deposit_lines:
        return Decimal('0.00')

    remaining = q(payment_amount)
    total_allocated = Decimal('0.00')

    for line in deposit_lines:
        if remaining <= 0:
            break
        alloc = min(remaining, q(line.amount))
        deposit = line.deposit
        if deposit and not deposit.paid_at:
            deposit.amount_held = q(alloc)
            deposit.paid_at = timezone.now()
            deposit.save(update_fields=['amount_held', 'paid_at'])

            LedgerEntry.objects.create(
                lease=deposit.lease,
                tenant=deposit.tenant,
                invoice=invoice,
                deposit=deposit,
                debit=Decimal('0.00'),
                credit=alloc,
                entry_type=LedgerEntry.DEPOSIT,
                description=f"Deposit payment from Invoice #{invoice.id} - Payment #{payment_record.pk}"
            )

        remaining = q(remaining - alloc)
        total_allocated = q(total_allocated + alloc)

    return total_allocated




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
def apply_credit_and_deposit(
    tenant,
    payment_amount: Decimal = None,
    reference: str = None,
    method: str = "Mpesa",
    apply_to_deposit: bool = True,
    invoice=None,
    use_logger: bool = True
):
    """
    ROBUST PAYMENT PROCESSING - Payment Records Only Approach
    
    This function applies payments or tenant credits to invoices using ONLY Payment records.
    No more negative InvoiceLines are created - all payments are tracked via Payment objects.
    
    Key Changes:
    1. Payments are recorded as Payment objects linked to specific invoices
    2. Deposit tracking is updated via Deposit.amount_held, not negative invoice lines
    3. Balance calculations rely entirely on Payment records
    4. Cleaner separation: InvoiceLines = charges, Payment = payments
    
    Args:
        tenant: Tenant object to process payment for
        payment_amount: Amount being paid (None means use existing credit)
        reference: Payment reference/transaction ID
        method: Payment method (Mpesa, Cash, etc.)
        apply_to_deposit: Whether to prioritize deposit payments
        invoice: Specific invoice to prioritize (optional)
        use_logger: Whether to use logger for debugging
    
    Returns:
        dict: Summary of payment allocation
    """
    if not tenant:
        raise ValueError("Tenant is required")
    
    if not hasattr(tenant, 'pk') or not tenant.pk:
        raise ValueError("Invalid tenant object - missing primary key")
    
    # Verify tenant exists in database
    try:
        Tenant.objects.get(pk=tenant.pk)
    except Tenant.DoesNotExist:
        raise ValueError(f"Tenant with ID {tenant.pk} does not exist")
    
    def _quantize(value):
        if value is None:
            return Decimal('0.00')
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def log(msg):
        if use_logger:
            logger.info(msg)
        else:
            print(msg)

    # Initialize tracking variables
    original_payment = _quantize(payment_amount) if payment_amount is not None else None
    payment_left = original_payment
    applied_to_invoices = Decimal("0.00")
    applied_to_deposit = Decimal("0.00")
    stored_as_credit = Decimal("0.00")
    
    tenant_balance_obj, _ = TenantBalance.objects.get_or_create(tenant=tenant)
    active_lease = Lease.objects.filter(tenant=tenant, is_active=True).first()

    log(f"=== ROBUST PAYMENT PROCESSING START ===")
    log(f"Tenant: {tenant.full_name}")
    log(f"Original payment: {original_payment}")
    log(f"Method: {method}")

    # STEP 0: Determine payment source and available amount
    if payment_left is None:
        # Using existing tenant credit - find unallocated payments
        
        unallocated_payments = Payment.objects.filter(
            tenant=tenant,
            invoice__isnull=True  # Payments not yet allocated to specific invoices
        ).order_by('payment_date')  # FIFO for credit consumption
        
        available_credit = sum(_quantize(p.amount) for p in unallocated_payments)
        payment_source = "TenantCredit"
        using_tenant_credit = True
        log(f"Using unallocated payments as credit: {available_credit}")
        
        if available_credit <= 0:
            log("No unallocated payments available")
            return {
                "applied_to_deposit": "0.00",
                "applied_to_invoices": "0.00", 
                "stored_as_credit": "0.00",
                "unallocated": "0.00"
            }
    else:
        # Real payment received
        available_credit = payment_left
        payment_source = method
        using_tenant_credit = False
        log(f"Processing new payment: {payment_left}")

    # STEP 1: Get invoices to process (oldest first for consistent payment order)
    log(f"--- STEP 1: DETERMINING INVOICE ORDER ---")
    
    if invoice:
        # Prioritize specified invoice, then process others by age
        priority_invoice = Invoice.objects.filter(pk=invoice.pk, tenant=tenant, is_paid=False).first()
        other_invoices = Invoice.objects.filter(
            tenant=tenant, 
            is_paid=False
        ).exclude(pk=invoice.pk).order_by('billing_period_start', 'id')
        unpaid_invoices = ([priority_invoice] if priority_invoice else []) + list(other_invoices)
    else:
        # ROBUST: Always process oldest invoices first to clear backlogs
        unpaid_invoices = list(Invoice.objects.filter(
            tenant=tenant, 
            is_paid=False
        ).order_by('billing_period_start', 'id'))

    log(f"Found {len(unpaid_invoices)} unpaid invoices to process")

    # Track payment allocations for creating Payment records
    payment_allocations = []  # List of (invoice, amount) tuples

    # STEP 2: Process each invoice with deposit prioritization
    log(f"--- STEP 2: PROCESSING INVOICES ---")
    
    for inv in unpaid_invoices:
        if available_credit <= 0:
            break

        # Refresh invoice to get current balance
        inv.refresh_from_db()
        invoice_balance = _quantize(inv.balance)

        if invoice_balance <= 0:
            log(f"Invoice {inv.pk} already paid (balance: {invoice_balance}) - skipping")
            continue

        # Calculate how much to allocate to this invoice
        allocate_to_invoice = min(available_credit, invoice_balance)
        allocate_to_invoice = _quantize(allocate_to_invoice)

        if allocate_to_invoice <= 0:
            continue

        log(f"Invoice {inv.pk}: balance={invoice_balance}, allocating={allocate_to_invoice}")

        # ROBUST DEPOSIT PRIORITIZATION: Handle deposits first within this invoice
        remaining_allocation = allocate_to_invoice
        deposit_allocation = Decimal("0.00")
        
        if apply_to_deposit:
            # Find unpaid deposit lines in this invoice
            deposit_lines = inv.lines.filter(line_type=InvoiceLine.LINE_DEPOSIT).order_by('id')
            
            for deposit_line in deposit_lines:
                if remaining_allocation <= 0:
                    break
                
                # Check if this deposit needs payment
                if deposit_line.deposit:
                    deposit_needed = _quantize(
                        deposit_line.deposit.amount - deposit_line.deposit.amount_held
                    )
                    
                    if deposit_needed > 0:
                        # Calculate how much to pay toward this deposit
                        pay_toward_deposit = min(remaining_allocation, deposit_needed)
                        
                        log(f"  → Paying {pay_toward_deposit} toward deposit (needed: {deposit_needed})")
                        
                        # ROBUST: Update deposit record directly (no negative invoice lines)
                        new_amount_held = _quantize(
                            deposit_line.deposit.amount_held + pay_toward_deposit
                        )
                        deposit_line.deposit.amount_held = new_amount_held
                        
                        # Mark deposit as paid if fully satisfied
                        if deposit_line.deposit.amount_held >= deposit_line.deposit.amount:
                            if not deposit_line.deposit.paid_at:
                                deposit_line.deposit.paid_at = timezone.now()
                                log(f"  ✓ Deposit #{deposit_line.deposit.pk} fully paid")
                        
                        deposit_line.deposit.save(update_fields=['amount_held', 'paid_at'])
                        
                        # Track deposit allocation for summary
                        deposit_allocation = _quantize(deposit_allocation + pay_toward_deposit)
                        remaining_allocation = _quantize(remaining_allocation - pay_toward_deposit)

        # ROBUST: Create single Payment record for this invoice
        if allocate_to_invoice > 0:
            payment_type = 'DEPOSIT' if deposit_allocation == allocate_to_invoice else \
                          'RENT' if deposit_allocation == 0 else 'MIXED'
            
            if not using_tenant_credit:
                # Create new Payment record for real payment
                payment_record = Payment.objects.create(
                    tenant=tenant,
                    invoice=inv,
                    amount=allocate_to_invoice,
                    method=method,
                    reference=reference or f"Payment for Invoice {inv.pk}",
                    payment_type=payment_type
                )
                log(f"✓ Created Payment record #{payment_record.pk}: {allocate_to_invoice} for Invoice {inv.pk}")
            else:
                # Allocate existing unallocated payments to this invoice
                remaining_to_allocate = allocate_to_invoice
                
                for unallocated_payment in unallocated_payments:
                    if remaining_to_allocate <= 0:
                        break
                    
                    payment_amount_available = _quantize(unallocated_payment.amount)
                    if payment_amount_available <= 0:
                        continue
                    
                    to_use = min(payment_amount_available, remaining_to_allocate)
                    
                    if to_use < payment_amount_available:
                        # Partially allocate this payment: create new record for used portion
                        Payment.objects.create(
                            tenant=tenant,
                            invoice=inv,
                            amount=to_use,
                            method=unallocated_payment.method,
                            reference=f"Credit allocation to Invoice {inv.pk}",
                            payment_type=payment_type
                        )
                        # Reduce original unallocated payment
                        unallocated_payment.amount = _quantize(payment_amount_available - to_use)
                        unallocated_payment.save(update_fields=['amount'])
                        log(f"✓ Partially allocated Payment #{unallocated_payment.pk}: {to_use} to Invoice {inv.pk}")
                    else:
                        # Fully allocate this payment to the invoice
                        unallocated_payment.invoice = inv
                        unallocated_payment.payment_type = payment_type
                        unallocated_payment.reference = f"Credit allocation to Invoice {inv.pk}"
                        unallocated_payment.save(update_fields=['invoice', 'payment_type', 'reference'])
                        log(f"✓ Fully allocated Payment #{unallocated_payment.pk}: {to_use} to Invoice {inv.pk}")
                    
                    remaining_to_allocate = _quantize(remaining_to_allocate - to_use)

        # Update tracking totals
        applied_to_invoices = _quantize(applied_to_invoices + allocate_to_invoice)
        applied_to_deposit = _quantize(applied_to_deposit + deposit_allocation)
        available_credit = _quantize(available_credit - allocate_to_invoice)

        if not using_tenant_credit:
            payment_left = _quantize(payment_left - allocate_to_invoice)

        # ROBUST: Invoice balance is automatically updated through Payment.save() method
        # The invoice's balance property now correctly calculates using Payment records
        inv.refresh_from_db()
        if inv.balance <= 0 and not inv.is_paid:
            inv.mark_paid()
            log(f"✓ Invoice {inv.pk} marked as PAID (balance: {inv.balance})")

    log(f"Invoice processing complete: {applied_to_invoices} applied to {len(payment_allocations)} invoices")

    # STEP 3: Store any remaining payment as unallocated credit
    if not using_tenant_credit and payment_left > 0:
        log(f"--- STEP 3: STORING OVERPAYMENT ---")
        log(f"Storing {payment_left} as unallocated tenant credit")

        # ROBUST: Create unallocated Payment record (invoice=None)
        credit_payment = Payment.objects.create(
            tenant=tenant,
            invoice=None,  # Unallocated - can be used for future invoices
            amount=payment_left,
            method=method,
            reference=reference or "Overpayment credit",
            payment_type='CREDIT'
        )
        
        log(f"✓ Created unallocated Payment record #{credit_payment.pk}: {payment_left}")
        stored_as_credit = payment_left
        payment_left = Decimal("0.00")

    # STEP 4: Recalculate tenant balance using robust method
    log(f"--- STEP 4: RECALCULATING TENANT BALANCE ---")
    try:
        tenant_balance_obj = TenantBalance.recalc_for_tenant(tenant, use_logger=use_logger)
        log(f"New tenant balance: {tenant_balance_obj.balance}")
    except Exception as e:
        logger.error(f"Balance recalculation failed: {e}")
        # Fallback to simple balance calculation
        tenant_balance_obj, _ = TenantBalance.objects.get_or_create(
            tenant=tenant, 
            defaults={'balance': Decimal('0.00')}
        )

    # STEP 5: Generate final summary
    final_summary = {
        "applied_to_deposit": str(applied_to_deposit),
        "applied_to_invoices": str(applied_to_invoices),
        "stored_as_credit": str(stored_as_credit),
        "unallocated": str(payment_left),
        "tenant_balance": str(tenant_balance_obj.balance)
    }

    log(f"=== ROBUST PAYMENT PROCESSING COMPLETE ===")
    log(f"Applied to deposits: {applied_to_deposit}")
    log(f"Applied to invoices: {applied_to_invoices}")
    log(f"Stored as credit: {stored_as_credit}")
    log(f"Remaining unallocated: {payment_left}")
    log(f"Final tenant balance: {tenant_balance_obj.balance}")

    return final_summary



# @transaction.atomic
# def apply_credit_and_deposit(
#     tenant,
#     payment_amount: Decimal = None,
#     reference: str = None,
#     method: str = "Mpesa",
#     apply_to_deposit: bool = True,
#     invoice=None,
#     use_logger: bool = True
# ):
#     """
#     Apply payment or tenant credits to invoices properly.
#     Key improvement: Creates ONE Payment record per actual payment received,
#     then tracks allocation internally via ledger entries.
#     """
#     if not tenant:
#         raise ValueError("Tenant is required")
    
#     if not hasattr(tenant, 'pk') or not tenant.pk:
#         raise ValueError("Invalid tenant object - missing primary key")
    
#     # Verify tenant exists in database
#     try:
#         Tenant.objects.get(pk=tenant.pk)
#     except Tenant.DoesNotExist:
#         raise ValueError(f"Tenant with ID {tenant.pk} does not exist")
    
#     def _quantize(value):
#         if value is None:
#             return Decimal('0.00')
#         return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

#     def log(msg):
#         if use_logger:
#             logger.info(msg)
#         else:
#             print(msg)

#     original_payment = _quantize(payment_amount) if payment_amount is not None else None
#     payment_left = original_payment
#     applied_to_invoices = Decimal("0.00")
#     applied_to_deposit = Decimal("0.00")
#     stored_as_credit = Decimal("0.00")
    
#     tenant_balance_obj, _ = TenantBalance.objects.get_or_create(tenant=tenant)
#     active_lease = Lease.objects.filter(tenant=tenant, is_active=True).first()

#     log(f"=== PAYMENT PROCESSING START ===")
#     log(f"Tenant: {tenant.full_name}")
#     log(f"Original payment: {original_payment}")
#     log(f"Method: {method}")

#     # Determine if this is a real payment or using existing credit
#     if payment_left is None:
#         # Using existing tenant credit
#         unallocated_credit_entries = LedgerEntry.objects.filter(
#             tenant=tenant,
#             invoice__isnull=True,
#             deposit__isnull=True,
#             credit__gt=0
#         ).order_by('created_at')
        
#         available_credit = sum(_quantize(e.credit) for e in unallocated_credit_entries)
#         payment_source = "TenantBalance"
#         using_tenant_credit = True
#         log(f"Using tenant credit: {available_credit}")
#     else:
#         # Real payment received - CREATE THE PAYMENT RECORD UPFRONT
#         master_payment = Payment.objects.create(
#             tenant=tenant,
#             invoice=None,  # Will be updated if applied to single invoice
#             amount=original_payment,
#             method=method,
#             reference=reference or "Payment received"
#         )
#         log(f"✓ Created master payment record {master_payment.pk} for {original_payment}")
        
#         available_credit = payment_left
#         payment_source = method
#         using_tenant_credit = False
#         log(f"Using real payment: {payment_left}")

#     if available_credit <= 0:
#         log("No credit/payment available")
#         return {
#             "applied_to_deposit": "0.00",
#             "applied_to_invoices": "0.00", 
#             "stored_as_credit": "0.00",
#             "unallocated": "0.00"
#         }

#     # STEP 1: Determine invoice ordering (billing-day-aware for real payments)
#     log(f"--- STEP 1: APPLYING TO INVOICES ---")
    
#     if invoice:
#         # Prioritize specified invoice
#         priority_invoice = Invoice.objects.filter(pk=invoice.pk, tenant=tenant, is_paid=False).first()
#         other_invoices = Invoice.objects.filter(
#             tenant=tenant, 
#             is_paid=False
#         ).exclude(pk=invoice.pk).order_by('billing_period_start', 'id')
#         unpaid_invoices = ([priority_invoice] if priority_invoice else []) + list(other_invoices)
#     else:
#         # In apply_credit_and_deposit function, around line 580-590
#     # Fix the invoice ordering for credit application

#     if not using_tenant_credit:
#         # For real payments, prefer the invoice whose billing period contains the payment date
#         payment_date = timezone.now().date()
#         billing_day = tenant.property.billing_day
#         pref_start, pref_end = billing_period_for_reading_date(payment_date, billing_day)
#         priority_invoice = Invoice.objects.filter(
#             tenant=tenant,
#             billing_period_start=pref_start,
#             billing_period_end=pref_end,
#             is_paid=False
#         ).first()

#         if priority_invoice:
#             other_invoices = Invoice.objects.filter(
#                 tenant=tenant,
#                 is_paid=False
#             ).exclude(pk=priority_invoice.pk).order_by('billing_period_start', 'id')
#             unpaid_invoices = [priority_invoice] + list(other_invoices)
#             log(f"Prioritizing invoice {priority_invoice.pk} for period {pref_start} → {pref_end}")
#         else:
#             # fallback oldest-first
#             unpaid_invoices = list(Invoice.objects.filter(
#                 tenant=tenant, 
#                 is_paid=False
#             ).order_by('billing_period_start', 'id'))
#     else:
#         # FIXED: For tenant-credit path, apply to NEWEST invoice first (LIFO)
#         # But we need to ensure we're getting the truly newest unpaid invoice
#         newest_unpaid = Invoice.objects.filter(
#             tenant=tenant, 
#             is_paid=False
#         ).order_by('-billing_period_start', '-id').first()
        
#         if newest_unpaid:
#             other_invoices = Invoice.objects.filter(
#                 tenant=tenant,
#                 is_paid=False
#             ).exclude(pk=newest_unpaid.pk).order_by('-billing_period_start', '-id')
#             unpaid_invoices = [newest_unpaid] + list(other_invoices)
#             log(f"Applying credit to newest invoice {newest_unpaid.pk} first")
#         else:
#             unpaid_invoices = []

#     log(f"Found {len(unpaid_invoices)} unpaid invoices")

#     # Track which invoices were paid for master payment record
#     paid_invoice_ids = []

#     # Process invoices
#     for inv in unpaid_invoices:
#         if available_credit <= 0:
#             break

#         inv.refresh_from_db()
#         invoice_balance = _quantize(inv.balance)

#         if invoice_balance <= 0:
#             log(f"Invoice {inv.pk} already paid - skipping")
#             continue

#         allocate = min(available_credit, invoice_balance)
#         allocate = _quantize(allocate)

#         if allocate <= 0:
#             continue

#         log(f"Invoice {inv.pk}: total_amount={inv.total_amount}, balance={invoice_balance}")
#         log(f"Allocating {allocate} to invoice")

#         if not using_tenant_credit:
#             # For real payments: Create ledger entry to track allocation
#             LedgerEntry.objects.create(
#                 lease=active_lease,
#                 tenant=tenant,
#                 invoice=inv,
#                 deposit=None,
#                 debit=Decimal("0.00"),
#                 credit=allocate,
#                 entry_type=LedgerEntry.RENT,
#                 description=f"Payment {master_payment.pk} applied to Invoice {inv.pk}"
#             )
            
#             # Track which invoice was paid
#             paid_invoice_ids.append(inv.pk)

#             # allocate to deposit lines if present
#             deposit_lines_total = inv.lines.filter(line_type=InvoiceLine.LINE_DEPOSIT).aggregate(
#                 total=Sum('amount')
#             )['total'] or Decimal('0.00')

#             if deposit_lines_total > 0:
#                 deposit_portion = min(allocate, _quantize(deposit_lines_total))
#                 applied_to_deposit = _quantize(applied_to_deposit + deposit_portion)
#                 # call helper to actually update deposit records / ledger
#                 try:
#                     allocated = allocate_payment_to_deposit_lines(inv, deposit_portion, master_payment)
#                     log(f"✓ {allocated} allocated to deposit lines (inv {inv.pk})")
#                 except Exception as e:
#                     logger.error(f"Failed to allocate deposit portion for payment {master_payment.pk}: {e}")

#         else:
#             # using tenant credit: mark existing credit entries as allocated
#             remaining_to_allocate = allocate
#             for entry in unallocated_credit_entries:
#                 if remaining_to_allocate <= 0:
#                     break

#                 entry_credit = _quantize(entry.credit)
#                 if entry_credit <= 0:
#                     continue

#                 to_use = min(entry_credit, remaining_to_allocate)

#                 if to_use < entry_credit:
#                     entry.credit = _quantize(entry_credit - to_use)
#                     entry.save(update_fields=["credit"])
#                     LedgerEntry.objects.create(
#                         lease=entry.lease,
#                         tenant=entry.tenant,
#                         invoice=inv,
#                         deposit=None,
#                         credit=to_use,
#                         debit=Decimal("0.00"),
#                         entry_type=entry.entry_type,
#                         description=f"Credit applied to Invoice {inv.pk}"
#                     )
#                 else:
#                     entry.invoice = inv
#                     entry.save(update_fields=["invoice"])

#                 remaining_to_allocate = _quantize(remaining_to_allocate - to_use)

#         # Update tracking
#         applied_to_invoices = _quantize(applied_to_invoices + allocate)
#         available_credit = _quantize(available_credit - allocate)

#         if not using_tenant_credit:
#             payment_left = _quantize(payment_left - allocate)

#         # Update invoice status and totals
#         inv.recalc_total()
#         if inv.balance <= 0 and not inv.is_paid:
#             inv.mark_paid()
#             log(f"✓ Invoice {inv.pk} marked as PAID")

#     # Update master payment record with invoice reference (if only one invoice was paid)
#     if not using_tenant_credit and len(paid_invoice_ids) == 1:
#         invoice_obj = Invoice.objects.get(pk=paid_invoice_ids[0])
#         master_payment.invoice = invoice_obj
#         master_payment.save(update_fields=['invoice'])
#         log(f"✓ Updated master payment {master_payment.pk} with invoice {invoice_obj.pk}")

#     log(f"Invoice processing complete: {applied_to_invoices} applied to invoices")

#     # STEP 2: Store any remaining overpayment as credit
#     if not using_tenant_credit and payment_left > 0:
#         log(f"--- STEP 2: STORING OVERPAYMENT ---")
#         log(f"Storing {payment_left} as tenant credit")

#         # DON'T create another Payment record - just create ledger entry for the credit
#         LedgerEntry.objects.create(
#             lease=active_lease,
#             tenant=tenant,
#             invoice=None,
#             deposit=None,
#             debit=Decimal("0.00"),
#             credit=payment_left,
#             entry_type=LedgerEntry.RENT,
#             description=f"Overpayment credit from payment {master_payment.pk} - Original: {original_payment}, Applied: {applied_to_invoices}, Credit: {payment_left}"
#         )

#         stored_as_credit = payment_left
#         payment_left = Decimal("0.00")

#     # STEP 3: Recalculate tenant balance
#     log(f"--- STEP 3: RECALCULATING BALANCE ---")
#     try:
#         tenant_balance_obj = TenantBalance.recalc_for_tenant(tenant, use_logger=use_logger)
#         log(f"New tenant balance: {tenant_balance_obj.balance}")
#     except Exception as e:
#         logger.error(f"Balance recalculation failed: {e}")
#         # Fallback - ensure we have a valid balance object
#         tenant_balance_obj, _ = TenantBalance.objects.get_or_create(
#             tenant=tenant, 
#             defaults={'balance': Decimal('0.00')}
#         )
#         log(f"Fallback tenant balance: {tenant_balance_obj.balance}")

#     final_summary = {
#         "applied_to_deposit": str(applied_to_deposit),
#         "applied_to_invoices": str(applied_to_invoices),
#         "stored_as_credit": str(stored_as_credit),
#         "unallocated": str(payment_left),
#         "tenant_balance": str(tenant_balance_obj.balance)
#     }

#     log(f"=== FINAL SUMMARY ===")
#     for key, value in final_summary.items():
#         log(f"{key}: {value}")

#     return final_summary



@transaction.atomic
def remove_water_invoice_line_for_deleted_reading(reading):
    """
    Remove water invoice lines associated with a deleted meter reading.
    Only removes if no other readings exist in the same billing period.
    """
    # Get active lease for the unit
    lease = Lease.objects.filter(unit=reading.unit, is_active=True).first()
    if not lease:
        return

    # Determine billing period for the reading
    billing_day = reading.unit.property.billing_day
    start, end = billing_period_for_reading_date(reading.reading_date, billing_day)
    
    # Check if other readings exist in the same period
    other_readings = MeterReading.objects.filter(
        unit=reading.unit,
        reading_date__range=(start, end),
        current_reading__isnull=False
    ).exists()
    
    # Only remove water lines if no other readings exist
    if not other_readings:
        InvoiceLine.objects.filter(
            meter_reading=reading,
            line_type=InvoiceLine.LINE_WATER
        ).delete()





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
