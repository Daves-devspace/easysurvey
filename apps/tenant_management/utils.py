import datetime
from calendar import monthrange
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Dict, Optional
from collections import defaultdict
import logging

from django.db.models import (
    OuterRef, Subquery, Value, DecimalField, F, Q, Prefetch, Exists
)
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.tenant_management.models import (
    MeterReading, Unit, Lease, Tenant, Payment, Deposit, InvoiceLine, WaterRate
)

logger = logging.getLogger(__name__)

CENTS = Decimal("0.01")

def q(amount: Optional[Decimal]) -> Decimal:
    """Quantize decimal to 2 places."""
    if amount is None:
        return Decimal("0.00")
    if not isinstance(amount, Decimal):
        try:
            amount = Decimal(str(amount))
        except Exception:
            return Decimal("0.00")
    return amount.quantize(CENTS, rounding=ROUND_HALF_UP)

# --- Rates Helper ---
def get_applicable_rate_for_date(water_company, on_date):
    if not water_company: return None
    return WaterRate.objects.filter(
        water_company=water_company,
        effective_from__lte=on_date
    ).filter(
        Q(effective_to__gte=on_date) | Q(effective_to__isnull=True)
    ).order_by('-effective_from').first()

# --- Meter Readings Logic (FIXED) ---
def filter_meter_readings_for_property(property_obj, month_str: Optional[str] = None) -> List[Dict]:
    """
    Returns list of dicts for the readings table.
    
    Fix Applied: 
    1. Prioritizes stored DB values (reading.usage, reading.amount) over on-the-fly math.
    2. Correctly determines 'previous_current' (baseline) for pending readings.
    """
    
    # 1. Determine Billing Period
    today = datetime.date.today()
    if month_str:
        try:
            year, month = map(int, month_str.split("-"))
        except ValueError:
            year, month = today.year, today.month
    else:
        year, month = today.year, today.month

    billing_start = datetime.date(year, month, 1)
    last_day = monthrange(year, month)[1]
    billing_end = datetime.date(year, month, last_day)

    # 2. Subqueries
    # Get the actual reading object ID if it exists for this month
    latest_reading_pk_sq = (
        MeterReading.objects
        .filter(unit=OuterRef("pk"), reading_date__range=(billing_start, billing_end))
        .order_by("-reading_date")
        .values("pk")[:1]
    )

    # Get the LAST reading before this month to serve as baseline (previous_reading)
    prev_current_sq = (
        MeterReading.objects
        .filter(unit=OuterRef("pk"), reading_date__lt=billing_start)
        .order_by("-reading_date")
        .values("current_reading")[:1]
    )

    # 3. Fetch Units with Annotations
    units_qs = (
        Unit.objects.filter(property=property_obj)
        .annotate(
            latest_reading_pk=Subquery(latest_reading_pk_sq),
            # This annotation calculates what the previous reading SHOULD be if we create a new one
            calculated_previous=Coalesce(
                Subquery(prev_current_sq, output_field=DecimalField(max_digits=14, decimal_places=2)),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        )
        .prefetch_related(
            Prefetch(
                "leases",
                queryset=Lease.objects.filter(is_active=True).select_related("tenant"),
                to_attr="active_leases_prefetched",
            )
        )
        .order_by("unit_number")
    )

    # 4. Bulk Fetch Actual Reading Objects
    latest_pks = [u.latest_reading_pk for u in units_qs if u.latest_reading_pk]
    readings_map = {}
    if latest_pks:
        # Fetch full objects so we access .usage, .amount, .previous_reading directly
        readings = MeterReading.objects.filter(pk__in=latest_pks)
        readings_map = {r.pk: r for r in readings}

    # 5. Build Result List
    results: List[Dict] = []
    
    for unit in units_qs:
        reading = readings_map.get(unit.latest_reading_pk)
        
        # Get Tenant
        tenant = None
        if getattr(unit, "active_leases_prefetched", None):
            tenant = unit.active_leases_prefetched[0].tenant

        if reading:
            # CASE A: Reading Exists (Filled)
            # Trust the DB values explicitly. Do not recalculate.
            status = "filled"
            previous_current = reading.previous_reading 
            current_val = reading.current_reading
            usage = reading.usage
            amount = reading.amount
            
            # Only fetch rate for display purposes if needed, not for math
            rate_val = None 
            if reading.rate_per_cubic_meter:
                rate_val = reading.rate_per_cubic_meter
            
        else:
            # CASE B: No Reading (Pending)
            # We need to suggest the 'Previous Reading' for the UI form
            status = "pending"
            previous_current = unit.calculated_previous
            current_val = None
            usage = None
            amount = None
            rate_val = None
            
            # Attempt to fetch current rate to show user what it WILL cost
            rate_obj = get_applicable_rate_for_date(unit.property.water_company, billing_end)
            if rate_obj:
                rate_val = getattr(rate_obj, "rate_per_cubic_meter", getattr(rate_obj, "rate_per_unit", None))

        results.append({
            "unit": unit,
            "tenant": tenant,
            "reading": reading, # The object (or None)
            "previous_current": q(previous_current),
            "current_reading": q(current_val) if current_val is not None else None,
            "usage": q(usage) if usage is not None else None,
            "rate": q(rate_val) if rate_val is not None else None,
            "amount": q(amount) if amount is not None else None,
            "status": status,
            "billing_start": billing_start, # Helpful for the form
            "billing_end": billing_end      # Helpful for the form
        })

    return results

# --- Property Leases Data (For Details Table) ---
def get_property_leases_data(property_obj):
    """
    Calculates financials for the 'Lease & Billing' tab.
    Aggregates invoices, payments, and deposits.
    """
    leases_qs = (
        Lease.objects
        .filter(unit__property=property_obj)
        .select_related("tenant", "unit")
        .order_by("unit__unit_number")
    )

    if not leases_qs.exists():
        return [], {"total_invoiced": 0, "total_paid": 0, "total_balance": 0, "total_deposit": 0}

    lease_ids = list(leases_qs.values_list("id", flat=True))

    # Bulk Fetch Data
    invoice_lines = list(InvoiceLine.objects.filter(lease__in=lease_ids).select_related("meter_reading"))
    payments = list(Payment.objects.filter(invoice__lines__lease__in=lease_ids).distinct())
    deposits = list(Deposit.objects.filter(lease__in=lease_ids))

    # Maps
    lines_by_lease = defaultdict(list)
    for l in invoice_lines: lines_by_lease[l.lease_id].append(l)

    deposits_by_lease = defaultdict(list)
    for d in deposits: deposits_by_lease[d.lease_id].append(d)

    payments_by_lease = defaultdict(list)
    # Complex mapping: Payment -> Invoice -> Lines -> Lease
    # We need to know which lease a payment belongs to via the invoice
    inv_to_lease_map = defaultdict(set)
    for line in invoice_lines:
        inv_to_lease_map[line.invoice_id].add(line.lease_id)
    
    for p in payments:
        if p.invoice_id:
            linked_leases = inv_to_lease_map.get(p.invoice_id, set())
            for lid in linked_leases:
                payments_by_lease[lid].append(p)
        elif p.tenant:
             # Fallback logic: if payment is unallocated but tenant matches lease
             # (This is tricky, simplified here to ignore unallocated for specific lease balance)
             pass

    # Construct Data
    leases_data = []
    for lease in leases_qs:
        lid = lease.id
        
        # Sums
        total_invoiced = sum(q(l.amount) for l in lines_by_lease[lid])
        total_deposit = sum(q(d.amount) for d in deposits_by_lease[lid])
        # Note: Logic for total_paid here is simplified; strictly it should split payments by line item ratio
        # but for summary view, summing payments linked to lease's invoice is acceptable approx.
        total_paid = sum(q(p.amount) for p in payments_by_lease[lid])
        
        # Water Stats
        water_lines = [l for l in lines_by_lease[lid] if l.line_type == 'WATER']
        total_water_usage = sum(q(l.meter_reading.usage) for l in water_lines if l.meter_reading)
        total_water_amount = sum(q(l.amount) for l in water_lines)

        leases_data.append({
            "lease_obj": lease,
            "tenant": lease.tenant,
            "unit": lease.unit,
            "rent_amount": lease.unit.rent_amount,
            "deposit": total_deposit,
            "status": "Active" if lease.is_active else "Inactive",
            "total_invoiced": total_invoiced,
            "total_paid": total_paid,
            "balance": q(total_invoiced - total_paid),
            "total_water_usage": total_water_usage,
            "total_water_amount": total_water_amount,
            "lease_start": lease.start_date,
            "lease_end": lease.end_date,
            "unleased": False
        })

    # Aggregates
    agg = {
        "total_invoiced": sum(d['total_invoiced'] for d in leases_data),
        "total_paid": sum(d['total_paid'] for d in leases_data),
        "total_balance": sum(d['balance'] for d in leases_data),
        "total_deposit": sum(d['deposit'] for d in leases_data),
    }

    return leases_data, agg

def filter_units_for_property(property_obj, status=None):
    """Returns units with has_active_lease annotation."""
    qs = Unit.objects.filter(property=property_obj)
    active_lease_exists = Lease.objects.filter(unit=OuterRef('pk'), is_active=True)
    qs = qs.annotate(has_active_lease=Exists(active_lease_exists))

    if status == 'occupied':
        qs = qs.filter(has_active_lease=True)
    elif status == 'vacant':
        qs = qs.filter(has_active_lease=False)
    
    return qs.order_by('unit_number')