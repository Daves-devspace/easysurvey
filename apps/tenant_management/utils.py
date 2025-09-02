# apps/tenant_management/utils.py
import datetime
from calendar import monthrange
from django.db.models import QuerySet
from apps.tenant_management.models import MeterReading, Unit, Property, Lease
from django.db.models import OuterRef, Subquery, Value, DecimalField, IntegerField,Prefetch
from django.db.models.functions import Coalesce
from decimal import Decimal, ROUND_HALF_UP
import calendar
from typing import Optional
from django.db.models import F, ExpressionWrapper
from apps.tenant_management.billings.services import get_applicable_rate_for_date
from typing import List, Dict


def filter_units_for_property(property_obj, status=None):
    """
    Return a queryset of Units for the given property_obj, annotated with
    `has_active_lease` boolean.
    """
    from django.db.models import Exists, OuterRef
    from apps.tenant_management.models import Unit, Lease

    qs = Unit.objects.filter(property=property_obj)
    active_lease_qs = Lease.objects.filter(unit=OuterRef('pk'), is_active=True)
    qs = qs.annotate(has_active_lease=Exists(active_lease_qs))

    if status is None or status == 'all':
        filtered = qs
    elif status == 'occupied':
        filtered = qs.filter(has_active_lease=True)
    elif status == 'vacant':
        filtered = qs.filter(has_active_lease=False)
    else:
        filtered = qs

    return filtered.order_by('unit_number')





CENTS = Decimal("0.01")
def q(amount: Optional[Decimal]) -> Decimal:
    if amount is None:
        return Decimal("0.00")
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))
    return amount.quantize(CENTS, rounding=ROUND_HALF_UP)


def filter_meter_readings_for_property(property_obj, month_str: Optional[str] = None) -> List[Dict]:
    """
    Return a list (one per unit) with:
      - unit
      - tenant (active lease tenant or None)
      - reading (latest MeterReading in the billing month or None)
      - previous_current (baseline – last current_reading before billing_start; 0 if none)
      - usage (Decimal or None)
      - rate (Decimal or None)
      - amount (Decimal or None)
      - status ("pending" if no reading exists for the billing period, else "filled")

    This will **always** include ALL units in the property (so you can show 'Add' controls).
    Designed to be efficient: one query to annotate units, one query to fetch latest readings.
    """

    # --- Determine billing month (default = current month) ---
    today = datetime.date.today()
    if month_str:
        try:
            year, month = map(int, month_str.split("-"))
        except Exception:
            year, month = today.year, today.month
    else:
        year, month = today.year, today.month

    billing_start = datetime.date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    billing_end = datetime.date(year, month, last_day)

    # --- Subqueries: latest reading in billing period & previous reading before billing_start ---
    latest_reading_pk_sq = (
        MeterReading.objects
        .filter(unit=OuterRef("pk"), reading_date__range=(billing_start, billing_end))
        .order_by("-reading_date")
        .values("pk")[:1]
    )

    prev_current_sq = (
        MeterReading.objects
        .filter(unit=OuterRef("pk"), reading_date__lt=billing_start)
        .order_by("-reading_date")
        .values("current_reading")[:1]
    )

    # --- Base queryset: units of property with annotations & prefetched active leases ---
    units_qs = (
        Unit.objects.filter(property=property_obj)
        .annotate(
            latest_reading_pk=Subquery(latest_reading_pk_sq),
            previous_current=Coalesce(
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

    # --- Bulk fetch latest readings (avoid N+1) ---
    latest_pks = [u.latest_reading_pk for u in units_qs if u.latest_reading_pk]
    readings_map = {}
    if latest_pks:
        readings = MeterReading.objects.filter(pk__in=latest_pks).select_related("unit")
        readings_map = {r.pk: r for r in readings}

    # --- Build results list ---
    results: List[Dict] = []
    for unit in units_qs:
        reading = readings_map.get(unit.latest_reading_pk)
        tenant = None
        if getattr(unit, "active_leases_prefetched", None):
            if unit.active_leases_prefetched:
                tenant = unit.active_leases_prefetched[0].tenant

        previous_current = q(unit.previous_current)
        usage = None
        rate_val = None
        amount = None

        if reading:
            # Calculate usage & amount if a reading exists
            usage = q((reading.current_reading or Decimal("0.00")) - previous_current)
            rate_obj = get_applicable_rate_for_date(unit.property.water_company, billing_end)
            if rate_obj:
                # Guard against multiple naming conventions
                rate_val = q(getattr(rate_obj, "rate_per_cubic_meter", getattr(rate_obj, "rate_per_unit", None)))
                amount = q(usage * rate_val)

        # --- Derived status: "pending" if no reading exists, else "filled" ---
        status = "pending" if reading is None else "filled"

        results.append({
            "unit": unit,
            "tenant": tenant,
            "reading": reading,
            "previous_current": previous_current,
            "usage": usage,
            "rate": rate_val,
            "amount": amount,
            "status": status,
        })

    return results
