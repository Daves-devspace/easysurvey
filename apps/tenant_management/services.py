# apps/tenant_management/services.py

from decimal import Decimal
from collections import defaultdict

from django.db.models import Sum, Value, DecimalField, Prefetch
from django.db.models.functions import Coalesce

from apps.tenant_management.models import (
    Lease, Payment, MeterReading, Tenant, Deposit,
)
from apps.tenant_management.models import InvoiceLine, Invoice


def get_property_leases_data(property_obj):
    """
    Return (leases_data_list, aggregates_dict) where per-lease totals are computed
    exactly the same way as in TenantDetailView (in-memory sums over pre-fetched
    invoice lines, payments and deposits), to avoid subquery aggregation mismatches.
    """

    # --- Fetch leases for the property (ordered) ---
    leases_qs = (
        Lease.objects
        .filter(unit__property=property_obj)
        .select_related("tenant", "unit")
        .order_by("unit__unit_number")
    )

    lease_ids = list(leases_qs.values_list("id", flat=True))

    # --- Bulk fetch invoice lines, payments and deposits that relate to leases in this property ---
    invoice_lines = list(
        InvoiceLine.objects
        .filter(lease__in=lease_ids)
        .select_related("lease", "invoice", "meter_reading")
    )

    # Payments for invoices that have lines for these leases.
    # We need the Payment objects; we will check their invoice lines to decide which lease(s) they apply to.
    payments = list(
        Payment.objects
        .filter(invoice__lines__lease__in=lease_ids)
        .select_related("invoice")
        .distinct()
    )

    # Deposits connected to leases on this property
    deposits = list(
        Deposit.objects
        .filter(lease__in=lease_ids)
        .select_related("lease", "tenant")
    )

    # --- Build quick lookup maps by lease id (safely summing in Python) ---
    invoice_lines_by_lease = defaultdict(list)
    for line in invoice_lines:
        if line.lease_id:
            invoice_lines_by_lease[line.lease_id].append(line)

    # For payments: a payment belongs to an invoice; an invoice may have lines for multiple leases.
    # The TenantDetailView counted a payment for a lease if the payment's invoice has any line with lease_id == lease.pk.
    payments_by_lease = defaultdict(list)
    # To avoid double-counting a payment for the same lease multiple times if invoice has multiple lines for same lease,
    # we will ensure each payment is only counted once per lease.
    for p in payments:
        # collect unique lease ids present on this payment's invoice lines that belong to our lease_ids
        invoice_line_lease_ids = {
            il.lease_id for il in p.invoice.lines.all() if il.lease_id in lease_ids
        } if hasattr(p.invoice, "lines") else set()
        # If invoice.lines wasn't prefetched, fallback to DB query (safe)
        if not invoice_line_lease_ids:
            invoice_line_lease_ids = set(
                InvoiceLine.objects.filter(invoice=p.invoice, lease__in=lease_ids)
                .values_list("lease_id", flat=True)
            )

        for lid in invoice_line_lease_ids:
            payments_by_lease[lid].append(p)

    # Deposits by lease
    deposits_by_lease = defaultdict(list)
    for d in deposits:
        if d.lease_id:
            deposits_by_lease[d.lease_id].append(d)

    # --- Helper to sum amounts safely as Decimal ---
    def sum_amounts(iterable, attr="amount"):
        total = Decimal("0.00")
        for obj in iterable:
            val = getattr(obj, attr, None)
            if val is None:
                continue
            if not isinstance(val, Decimal):
                try:
                    val = Decimal(str(val))
                except Exception:
                    continue
            total += val
        return total

    # --- Build leases_data list in same shape as TenantDetailView ---
    leases_data = []

    # Preload latest meter readings per unit (to avoid per-lease DB hit)
    unit_ids = [lease.unit_id for lease in leases_qs]
    latest_meter_by_unit = {}
    if unit_ids:
        latest_readings = (
            MeterReading.objects
            .filter(unit__in=unit_ids)
            .order_by("unit_id", "-reading_date")  # ordering then pick first per unit below
            .select_related("unit")
        )
        # pick first per unit_id
        seen_units = set()
        for mr in latest_readings:
            if mr.unit_id in seen_units:
                continue
            latest_meter_by_unit[mr.unit_id] = mr
            seen_units.add(mr.unit_id)

    for lease in leases_qs:
        lid = lease.id

        # total invoiced for this lease (sum of invoice line.amounts)
        total_invoiced = sum_amounts(invoice_lines_by_lease.get(lid, []), "amount")

        # total deposit for this lease
        total_deposit = sum_amounts(deposits_by_lease.get(lid, []), "amount")

        # total_paid: sum of payment.amount for payments whose invoice has a line belonging to this lease.
        # This mirrors TenantDetailView logic which counts whole payment amount if invoice contains a line for the lease.
        total_paid = sum_amounts(payments_by_lease.get(lid, []), "amount")

        # latest meter reading
        latest_reading = latest_meter_by_unit.get(lease.unit_id)
        previous_meter = latest_reading.previous_reading if latest_reading else None
        current_meter = latest_reading.current_reading if latest_reading else None

        # water lines & totals (from invoice_lines)
        water_lines = [line for line in invoice_lines_by_lease.get(lid, []) if getattr(line, "meter_reading", None)]
        total_water_usage = sum((line.meter_reading.usage or Decimal("0.00")) for line in water_lines)
        total_water_amount = sum((line.amount or Decimal("0.00")) for line in water_lines)

        leases_data.append({
            "lease_obj": lease,
            "tenant": lease.tenant,
            "unit": lease.unit,
            "rent_amount": lease.unit.rent_amount or Decimal("0.00"),
            "deposit": total_deposit,
            "status": "Active" if lease.is_active else "Expired",
            "total_invoiced": total_invoiced,
            "total_paid": total_paid,
            "balance": total_invoiced - total_paid,
            "balance_abs": abs(total_invoiced - total_paid),
            "previous_meter": previous_meter,
            "current_meter": current_meter,
            "water_lines": water_lines,
            "total_water_usage": total_water_usage,
            "total_water_amount": total_water_amount,
            "lease_start": lease.start_date,
            "lease_end": getattr(lease, "end_date", None),
            "unleased": False,
        })

    # --- Include unleased tenants if model supports Tenant.property ---
    tenants_with_lease_ids = [r["tenant"].id for r in leases_data]
    unleased_tenants = Tenant.objects.none()
    try:
        Tenant._meta.get_field("property")
        unleased_tenants = Tenant.objects.filter(property=property_obj).exclude(id__in=tenants_with_lease_ids)
    except Exception:
        pass

    for tenant in unleased_tenants:
        leases_data.append({
            "lease_obj": None,
            "tenant": tenant,
            "unit": None,
            "rent_amount": None,
            "deposit": Decimal("0.00"),
            "total_invoiced": Decimal("0.00"),
            "total_paid": Decimal("0.00"),
            "balance": Decimal("0.00"),
            "current_meter": None,
            "lease_start": None,
            "lease_end": None,
            "unleased": True,
        })

    # --- Aggregates consistent with tenant view ---
    total_invoiced_agg = sum((r.get("total_invoiced") or Decimal("0.00")) for r in leases_data)
    total_paid_agg = sum((r.get("total_paid") or Decimal("0.00")) for r in leases_data)
    total_balance_agg = sum((r.get("balance") or Decimal("0.00")) for r in leases_data)
    total_deposit_agg = sum((r.get("deposit") or Decimal("0.00")) for r in leases_data)

    aggregates = {
        "total_invoiced": total_invoiced_agg,
        "total_paid": total_paid_agg,
        "total_balance": total_balance_agg,
        "total_deposit": total_deposit_agg,
    }

    return leases_data, aggregates
