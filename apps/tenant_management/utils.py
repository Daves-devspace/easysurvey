import datetime
from calendar import monthrange
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Dict, Optional
import logging
from datetime import date
from django.db.models import Exists
from django.db.models import (
    OuterRef, Subquery, Value, DecimalField, F, Q, Prefetch, Sum
)
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.tenant_management.models import (
    MeterReading, Unit, Lease, Tenant, Payment, Deposit, InvoiceLine, WaterRate, LedgerEntry, Invoice
)

logger = logging.getLogger(__name__)
CENTS = Decimal("0.01")

def q(amount: Optional[Decimal]) -> Decimal:
    """Helper to quantize decimals."""
    if amount is None: return Decimal("0.00")
    if not isinstance(amount, Decimal):
        try: amount = Decimal(str(amount))
        except: return Decimal("0.00")
    return amount.quantize(CENTS, rounding=ROUND_HALF_UP)

# ... [get_applicable_rate_for_date, filter_meter_readings_for_property unchanged] ...
def get_applicable_rate_for_date(water_company, on_date):
    if not water_company: return None
    return WaterRate.objects.filter(
        water_company=water_company,
        effective_from__lte=on_date
    ).filter(
        Q(effective_to__gte=on_date) | Q(effective_to__isnull=True)
    ).order_by('-effective_from').first()

def filter_meter_readings_for_property(property_obj, month_str: Optional[str] = None) -> List[Dict]:
    today = datetime.date.today()
    if month_str:
        try: year, month = map(int, month_str.split("-"))
        except ValueError: year, month = today.year, today.month
    else: year, month = today.year, today.month
    
    billing_start = datetime.date(year, month, 1)
    last_day = monthrange(year, month)[1]
    billing_end = datetime.date(year, month, last_day)

    latest_reading_pk_sq = MeterReading.objects.filter(unit=OuterRef("pk"), reading_date__range=(billing_start, billing_end)).order_by("-id").values("pk")[:1]
    prev_current_sq = MeterReading.objects.filter(unit=OuterRef("pk"), reading_date__lt=billing_start).order_by("-reading_date", "-id").values("current_reading")[:1]

    units_qs = Unit.objects.filter(property=property_obj).annotate(
        latest_reading_pk=Subquery(latest_reading_pk_sq),
        calculated_previous=Coalesce(
            Subquery(prev_current_sq, output_field=DecimalField(max_digits=14, decimal_places=2)),
            Value(Decimal("0.00")),
            output_field=DecimalField(max_digits=14, decimal_places=2)
        )
    ).prefetch_related(
        Prefetch("leases", queryset=Lease.objects.filter(is_active=True).select_related("tenant"), to_attr="active_leases_prefetched")
    ).order_by("unit_number")

    latest_pks = [u.latest_reading_pk for u in units_qs if u.latest_reading_pk]
    readings_map = {r.pk: r for r in MeterReading.objects.filter(pk__in=latest_pks)} if latest_pks else {}

    results = []
    for unit in units_qs:
        reading = readings_map.get(unit.latest_reading_pk)
        tenant = unit.active_leases_prefetched[0].tenant if getattr(unit, "active_leases_prefetched", None) else None
        
        status = "pending"
        previous_current = Decimal("0.00")
        current_val = None; usage = None; amount = None; rate_val = None
        reading_obj_for_template = None
        is_baseline = reading and reading.usage == Decimal('0.00')

        if reading and not is_baseline:
            status = "filled"
            previous_current = reading.previous_reading 
            current_val = reading.current_reading
            usage = reading.usage
            amount = reading.amount
            if reading.rate_per_cubic_meter: rate_val = reading.rate_per_cubic_meter
            else:
                rate_obj = get_applicable_rate_for_date(unit.property.water_company, reading.reading_date)
                if rate_obj: rate_val = getattr(rate_obj, "rate_per_cubic_meter", getattr(rate_obj, "rate_per_unit", None))
            reading_obj_for_template = reading
        else:
            status = "pending"
            if is_baseline: previous_current = reading.current_reading
            else: previous_current = unit.calculated_previous
            rate_obj = get_applicable_rate_for_date(unit.property.water_company, billing_end)
            if rate_obj: rate_val = getattr(rate_obj, "rate_per_cubic_meter", getattr(rate_obj, "rate_per_unit", None))
            reading_obj_for_template = None 

        results.append({
            "unit": unit, "tenant": tenant, "reading": reading_obj_for_template, 
            "previous_current": q(previous_current), "current_reading": q(current_val) if current_val is not None else None,
            "usage": q(usage) if usage is not None else None, "rate": q(rate_val) if rate_val is not None else None, 
            "amount": q(amount) if amount is not None else None, "status": status,
        })
    return results

def get_tenant_leases_data(tenant):
    """
    Calculates financials for a specific Tenant using Prefetch + Python Sum.
    This avoids Cartesian product issues in SQL aggregation.
    """
    
    # 1. Fetch Leases with optimized prefetching
    # We fetch all related objects in bulk to minimize queries
    
    # Filter for real payments (not mixed/master)
    payments_qs = Payment.objects.exclude(payment_type='MIXED')
    
    # Prefetch Invoices and their Payments
    invoices_qs = Invoice.objects.all().prefetch_related(
        Prefetch('payments', queryset=payments_qs)
    )

    leases_qs = Lease.objects.filter(tenant=tenant).select_related(
        "unit", "unit__property"
    ).prefetch_related(
        'invoice_lines', # Direct link to lines (Total Invoiced)
        'deposits',      # Direct link to deposits (Deposit Held)
        Prefetch('invoices', queryset=invoices_qs) # Link to Invoices -> Payments (Total Paid)
    ).order_by("-start_date")
    
    # 2. Calculate Unallocated Credit (Wallet) via SQL (Safe single value)
    tenant_credit_val = Payment.objects.filter(
        tenant=tenant, 
        invoice__isnull=True
    ).exclude(payment_type='MIXED').aggregate(
        total=Coalesce(Sum("amount"), Value(0, output_field=DecimalField()))
    )['total']
    
    tenant_credit = q(tenant_credit_val)
    leases_data = []
    
    # 3. Calculate Totals in Python
    for lease in leases_qs:
        # A. Total Invoiced: Sum of all lines linked to this lease
        # This fixes the "0" issue if lines exist but Invoice.total_amount wasn't updated
        total_invoiced = sum(q(line.amount) for line in lease.invoice_lines.all())
        
        # B. Total Paid: Sum of payments on invoices linked to this lease
        # We iterate through the prefetched invoices and their payments
        total_paid = Decimal('0.00')
        for inv in lease.invoices.all():
            for p in inv.payments.all():
                total_paid += q(p.amount)
        
        # C. Deposit Info
        deposit_req = sum(q(d.amount) for d in lease.deposits.all())
        deposit_held = sum(q(d.amount_held) for d in lease.deposits.all())
        
        # D. Balance
        balance = total_invoiced - total_paid
        
        # Apply Credit Visual Fix (Only to Active Lease)
        if lease.is_active and tenant_credit > 0:
            balance -= tenant_credit

        # E. Meter Reading
        current_meter = None
        last_reading = MeterReading.objects.filter(unit=lease.unit).order_by('-reading_date').first()
        if last_reading:
            current_meter = last_reading.current_reading

        leases_data.append({
            "lease": lease,
            "lease_obj": lease, 
            "unit": lease.unit,
            "property": lease.unit.property,
            "rent_amount": lease.unit.rent_amount,
            
            "deposit": deposit_req,
            "deposit_held": deposit_held,
            
            "status": "Active" if lease.is_active else "Expired",
            "start_date": lease.start_date,
            "end_date": lease.end_date,
            
            "total_invoiced": total_invoiced,
            "total_paid": total_paid,
            
            "balance": balance,
            "balance_abs": abs(balance),
            
            "current_meter": current_meter,
        })

    # 4. Global Aggregates
    aggregates = {
        "total_invoiced": sum(d['total_invoiced'] for d in leases_data),
        "total_paid": sum(d['total_paid'] for d in leases_data),
        "total_balance": sum(d['balance'] for d in leases_data),
        "tenant_credit": tenant_credit,
        "total_deposit": sum(d['deposit'] for d in leases_data),
        "total_deposit_held": sum(d['deposit_held'] for d in leases_data),
        "total_units": len(leases_data)
    }

    return leases_data, aggregates

# ... [get_property_leases_data needs similar fix for robustness, but keeping simple for now] ...
def get_property_leases_data(property_obj):
    # This implementation matches the original requested robustness fix structure for property view
    leases_qs = Lease.objects.filter(unit__property=property_obj).select_related("tenant", "unit").order_by("unit__unit_number")
    # To keep it consistent, we should ideally use the same prefetch logic, 
    # but for property list scale, let's use the simpler aggregation if it works for you, 
    # or apply the same prefetch pattern. 
    # Applying prefetch pattern for consistency and accuracy:
    
    payments_qs = Payment.objects.exclude(payment_type='MIXED')
    invoices_qs = Invoice.objects.all().prefetch_related(Prefetch('payments', queryset=payments_qs))

    leases_qs = leases_qs.prefetch_related(
        'invoice_lines',
        'deposits',
        Prefetch('invoices', queryset=invoices_qs)
    )

    if not leases_qs.exists(): return [], {"total_invoiced": 0, "total_paid": 0, "total_balance": 0, "total_deposit": 0}

    leases_data = []
    # Meter readings usually need a separate optimized query or prefetch
    # For now, simple loop lookup (could be optimized)
    
    for lease in leases_qs:
        total_invoiced = sum(q(line.amount) for line in lease.invoice_lines.all())
        
        total_paid = Decimal('0.00')
        for inv in lease.invoices.all():
            for p in inv.payments.all():
                total_paid += q(p.amount)
        
        deposit_req = sum(q(d.amount) for d in lease.deposits.all())
        deposit_held = sum(q(d.amount_held) for d in lease.deposits.all())
        
        balance = total_invoiced - total_paid
        
        # Meter reading fetch
        last_reading = MeterReading.objects.filter(unit=lease.unit).order_by('-reading_date').first()
        current_meter = last_reading.current_reading if last_reading else None

        leases_data.append({
            "lease_obj": lease, "tenant": lease.tenant, "unit": lease.unit, "property": lease.unit.property,
            "rent_amount": lease.unit.rent_amount, 
            "deposit": deposit_req, "deposit_held": deposit_held,
            "status": "Active" if lease.is_active else "Inactive", "start_date": lease.start_date, "end_date": lease.end_date,
            "total_invoiced": total_invoiced, "total_paid": total_paid, "balance": balance, "balance_abs": abs(balance),
            "total_water_usage": 0, "total_water_amount": 0, # Simplified for property list view
            "current_meter": current_meter, "lease_start": lease.start_date, "lease_end": lease.end_date, "unleased": False
        })
        
    agg = {
        "total_invoiced": sum(d['total_invoiced'] for d in leases_data),
        "total_paid": sum(d['total_paid'] for d in leases_data),
        "total_balance": sum(d['balance'] for d in leases_data),
        "total_deposit": sum(d['deposit'] for d in leases_data),
    }
    return leases_data, agg

def filter_units_for_property(property_obj, status=None):
    qs = Unit.objects.filter(property=property_obj)
    active_lease_exists = Lease.objects.filter(unit=OuterRef('pk'), is_active=True)
    qs = qs.annotate(has_active_lease=Exists(active_lease_exists))
    if status == 'occupied': qs = qs.filter(has_active_lease=True)
    elif status == 'vacant': qs = qs.filter(has_active_lease=False)
    return qs.order_by('unit_number')