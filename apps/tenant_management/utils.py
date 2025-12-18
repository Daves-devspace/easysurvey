import datetime
from calendar import monthrange
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Dict, Optional
from collections import defaultdict
import logging

from django.db.models import (
    OuterRef, Subquery, Value, DecimalField, F, Q, Prefetch, Exists, Sum
)
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.tenant_management.models import (
    MeterReading, Unit, Lease, Tenant, Payment, Deposit, InvoiceLine, WaterRate, LedgerEntry
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

def get_applicable_rate_for_date(water_company, on_date):
    if not water_company: return None
    return WaterRate.objects.filter(
        water_company=water_company,
        effective_from__lte=on_date
    ).filter(
        Q(effective_to__gte=on_date) | Q(effective_to__isnull=True)
    ).order_by('-effective_from').first()

def filter_meter_readings_for_property(property_obj, month_str: Optional[str] = None) -> List[Dict]:
    """
    Returns list of dicts for the readings table.
    Ensures 'rate' key is ALWAYS present.
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

    # 2. Subqueries for Latest and Previous
    # Sort by '-id' allows "Append Only" logic to work even if dates are same
    latest_reading_pk_sq = (
        MeterReading.objects
        .filter(unit=OuterRef("pk"), reading_date__range=(billing_start, billing_end))
        .order_by("-id") 
        .values("pk")[:1]
    )

    prev_current_sq = (
        MeterReading.objects
        .filter(unit=OuterRef("pk"), reading_date__lt=billing_start)
        .order_by("-reading_date", "-id") 
        .values("current_reading")[:1]
    )

    # 3. Fetch Units
    units_qs = (
        Unit.objects.filter(property=property_obj)
        .annotate(
            latest_reading_pk=Subquery(latest_reading_pk_sq),
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

    # 4. Bulk Fetch Objects
    latest_pks = [u.latest_reading_pk for u in units_qs if u.latest_reading_pk]
    readings_map = {}
    if latest_pks:
        readings = MeterReading.objects.filter(pk__in=latest_pks)
        readings_map = {r.pk: r for r in readings}

    # 5. Build Result List
    results: List[Dict] = []
    
    for unit in units_qs:
        reading = readings_map.get(unit.latest_reading_pk)
        
        tenant = None
        if getattr(unit, "active_leases_prefetched", None):
            tenant = unit.active_leases_prefetched[0].tenant

        status = "pending"
        previous_current = Decimal("0.00")
        current_val = None
        usage = None
        amount = None
        rate_val = None
        reading_obj_for_template = None

        is_baseline = reading and reading.usage == Decimal('0.00')

        if reading and not is_baseline:
            status = "filled"
            previous_current = reading.previous_reading 
            current_val = reading.current_reading
            usage = reading.usage
            amount = reading.amount
            
            if reading.rate_per_cubic_meter:
                rate_val = reading.rate_per_cubic_meter
            else:
                rate_obj = get_applicable_rate_for_date(unit.property.water_company, reading.reading_date)
                if rate_obj:
                    rate_val = getattr(rate_obj, "rate_per_cubic_meter", getattr(rate_obj, "rate_per_unit", None))
            
            reading_obj_for_template = reading
            
        else:
            status = "pending"
            if is_baseline:
                previous_current = reading.current_reading
            else:
                previous_current = unit.calculated_previous

            rate_obj = get_applicable_rate_for_date(unit.property.water_company, billing_end)
            if rate_obj:
                rate_val = getattr(rate_obj, "rate_per_cubic_meter", getattr(rate_obj, "rate_per_unit", None))

            reading_obj_for_template = None 

        row_data = {
            "unit": unit,
            "tenant": tenant,
            "reading": reading_obj_for_template, 
            "previous_current": q(previous_current),
            "current_reading": q(current_val) if current_val is not None else None,
            "usage": q(usage) if usage is not None else None,
            "rate": q(rate_val) if rate_val is not None else None, 
            "amount": q(amount) if amount is not None else None,
            "status": status,
        }
        results.append(row_data)

    return results

def get_tenant_leases_data(tenant):
    """
    Calculates financials for a specific Tenant across all their leases.
    """
    leases_qs = Lease.objects.filter(tenant=tenant).select_related("unit", "unit__property").order_by("-start_date")
    if not leases_qs.exists(): return [], {}

    lease_ids = list(leases_qs.values_list("id", flat=True))
    unit_ids = list(leases_qs.values_list("unit_id", flat=True))

    latest_readings_map = {}
    if unit_ids:
        readings = MeterReading.objects.filter(unit__in=unit_ids).order_by('unit_id', '-reading_date').distinct('unit_id')
        for r in readings: latest_readings_map[r.unit_id] = r

    invoice_lines = list(InvoiceLine.objects.filter(lease__in=lease_ids).select_related("meter_reading", "invoice"))
    invoice_ids = {l.invoice_id for l in invoice_lines}
    
    payments = list(Payment.objects.filter(invoice_id__in=invoice_ids).exclude(payment_type='MIXED').distinct())
    deposits = list(Deposit.objects.filter(lease__in=lease_ids))

    lines_by_lease = defaultdict(list)
    for l in invoice_lines: lines_by_lease[l.lease_id].append(l)

    deposits_by_lease = defaultdict(list)
    for d in deposits: deposits_by_lease[d.lease_id].append(d)

    payments_by_lease = defaultdict(list)
    inv_to_lease = defaultdict(set)
    for l in invoice_lines: inv_to_lease[l.invoice_id].add(l.lease_id)
    
    for p in payments:
        if p.invoice_id:
            for lid in inv_to_lease[p.invoice_id]: 
                payments_by_lease[lid].append(p)

    leases_data = []
    for lease in leases_qs:
        lid = lease.id
        
        total_invoiced = sum(q(l.amount) for l in lines_by_lease[lid])
        total_deposit = sum(q(d.amount) for d in deposits_by_lease[lid])
        total_paid = sum(q(p.amount) for p in payments_by_lease[lid])
        
        deposit_required = sum(q(d.amount) for d in deposits_by_lease[lid])
        deposit_held = sum(q(d.amount_held) for d in deposits_by_lease[lid])
        
        water_lines = [l for l in lines_by_lease[lid] if l.line_type == 'WATER']
        total_water_usage = sum(q(l.meter_reading.usage) for l in water_lines if l.meter_reading)
        total_water_amount = sum(q(l.amount) for l in water_lines)
        
        reading_obj = latest_readings_map.get(lease.unit_id)
        current_meter = reading_obj.current_reading if reading_obj else None
        previous_meter = reading_obj.previous_reading if reading_obj else None

        leases_data.append({
            "lease": lease,
            "lease_obj": lease, 
            "unit": lease.unit,
            "property": lease.unit.property,
            "rent_amount": lease.unit.rent_amount,
            "deposit": deposit_required,
            "deposit_held": deposit_held,
            "status": "Active" if lease.is_active else "Expired",
            "start_date": lease.start_date,
            "end_date": lease.end_date,
            "total_invoiced": total_invoiced,
            "total_paid": total_paid,
            "balance": q(total_invoiced - total_paid),
            "balance_abs": abs(total_invoiced - total_paid),
            "total_water_usage": total_water_usage,
            "total_water_amount": total_water_amount,
            "current_meter": current_meter,
            "previous_meter": previous_meter,
            "water_lines": water_lines,
        })

    # --- Credit Calculation ---
    tenant_credit = Payment.objects.filter(
        tenant=tenant, 
        invoice__isnull=True
    ).exclude(payment_type='MIXED').aggregate(
        t=Coalesce(Sum("amount"), Value(Decimal("0.00")), output_field=DecimalField())
    )["t"]
    
    tenant_credit = q(tenant_credit)

    # --- Apply credit to Active Lease Balance for display ---
    if tenant_credit > 0:
        for ld in leases_data:
            if ld['status'] == 'Active':
                ld['balance'] -= tenant_credit
                ld['balance_abs'] = abs(ld['balance'])
                break 

    dep_qs = Deposit.objects.filter(tenant=tenant)
    total_deposit_held = dep_qs.aggregate(t=Coalesce(Sum("amount_held"), Value(Decimal("0.00")), output_field=DecimalField()))["t"]
    total_deposit_refunded = dep_qs.aggregate(t=Coalesce(Sum("refunded_amount"), Value(Decimal("0.00")), output_field=DecimalField()))["t"]

    aggregates = {
        "total_invoiced": sum(d['total_invoiced'] for d in leases_data),
        "total_paid": sum(d['total_paid'] for d in leases_data),
        "total_balance": sum(d['balance'] for d in leases_data),
        "tenant_credit": tenant_credit,
        "total_deposit": sum(d['deposit'] for d in leases_data),
        "total_deposit_held": total_deposit_held,
        "total_deposit_refunded": total_deposit_refunded,
        "total_units": len(leases_data)
    }

    return leases_data, aggregates

def get_property_leases_data(property_obj):
    leases_qs = Lease.objects.filter(unit__property=property_obj).select_related("tenant", "unit").order_by("unit__unit_number")
    if not leases_qs.exists(): 
        return [], {"total_invoiced": 0, "total_paid": 0, "total_balance": 0, "total_deposit": 0}
    
    lease_ids = list(leases_qs.values_list("id", flat=True))
    unit_ids = [l.unit_id for l in leases_qs]
    latest_readings_map = {}
    if unit_ids:
        readings = MeterReading.objects.filter(unit__in=unit_ids).order_by('unit_id', '-reading_date').values('unit_id', 'current_reading')
        for r in readings:
            if r['unit_id'] not in latest_readings_map:
                latest_readings_map[r['unit_id']] = r['current_reading']

    invoice_lines = list(InvoiceLine.objects.filter(lease__in=lease_ids).select_related("meter_reading"))
    payments = list(Payment.objects.filter(invoice__lines__lease__in=lease_ids).exclude(payment_type='MIXED').distinct())
    deposits = list(Deposit.objects.filter(lease__in=lease_ids))

    lines_by_lease = defaultdict(list)
    for l in invoice_lines: lines_by_lease[l.lease_id].append(l)
    deposits_by_lease = defaultdict(list)
    for d in deposits: deposits_by_lease[d.lease_id].append(d)
    payments_by_lease = defaultdict(list)
    inv_to_lease = defaultdict(set)
    for l in invoice_lines: inv_to_lease[l.invoice_id].add(l.lease_id)
    for p in payments:
        if p.invoice_id:
            for lid in inv_to_lease[p.invoice_id]: payments_by_lease[lid].append(p)

    leases_data = []
    for lease in leases_qs:
        lid = lease.id
        total_invoiced = sum(q(l.amount) for l in lines_by_lease[lid])
        total_deposit = sum(q(d.amount) for d in deposits_by_lease[lid])
        total_paid = sum(q(p.amount) for p in payments_by_lease[lid])
        water_lines = [l for l in lines_by_lease[lid] if l.line_type == 'WATER']
        total_water_usage = sum(q(l.meter_reading.usage) for l in water_lines if l.meter_reading)
        total_water_amount = sum(q(l.amount) for l in water_lines)
        current_meter = latest_readings_map.get(lease.unit_id)

        balance = q(total_invoiced - total_paid)
        
        leases_data.append({
            "lease_obj": lease,
            "tenant": lease.tenant,
            "unit": lease.unit,
            "rent_amount": lease.unit.rent_amount,
            "deposit": total_deposit,
            "deposit_held": sum(q(d.amount_held) for d in deposits_by_lease[lid]),
            "status": "Active" if lease.is_active else "Inactive",
            "total_invoiced": total_invoiced,
            "total_paid": total_paid,
            "balance": balance,
            "balance_abs": abs(balance), # Added balance_abs
            "total_water_usage": total_water_usage,
            "total_water_amount": total_water_amount,
            "current_meter": current_meter,
            "lease_start": lease.start_date,
            "lease_end": lease.end_date,
            "unleased": False
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