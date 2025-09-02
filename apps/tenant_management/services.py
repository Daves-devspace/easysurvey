# apps/tenant_management/services.py
from decimal import Decimal
from django.db.models import OuterRef, Subquery, Sum, Value, DecimalField, F
from django.db.models.functions import Coalesce
from apps.tenant_management.models import Lease, Payment, MeterReading, Tenant
from django.db.models import OuterRef, Subquery, Sum, Value, F
from django.db.models.functions import Coalesce
from decimal import Decimal

def get_property_leases_data(property_obj):
    """
    Returns (leases_data_list, aggregates_dict).

    leases_data_list: list of dicts with keys:
      - lease_obj (or None for unleased tenants)
      - tenant, unit, rent_amount (Decimal or None),
      - total_paid (Decimal), balance (Decimal or None),
      - current_meter, lease_start, lease_end, unleased (bool)

    aggregates: totals for deposit / paid / balance
    """

    # ---- Subquery: Total payments for each lease via invoice lines ----
    payments_sq = (
        Payment.objects
        .filter(invoice__lines__lease=OuterRef('pk'))
        .values('invoice__lines__lease')
        .annotate(
            total_paid=Coalesce(
                Sum('amount'),
                Value(Decimal('0.00')),
                output_field=DecimalField()
            )
        )
        .values('total_paid')[:1]
    )

    # ---- Subquery: latest meter reading for each unit ----
    latest_meter_sq = (
        MeterReading.objects
        .filter(unit=OuterRef('unit_id'))
        .order_by('-reading_date')
        .values('current_reading')[:1]
    )

    # ---- Base leases queryset ----
    leases_qs = (
        Lease.objects
        .filter(unit__property=property_obj)
        .select_related('tenant', 'unit')
        .annotate(
            total_paid=Coalesce(
                Subquery(payments_sq, output_field=DecimalField()),
                Value(Decimal('0.00')),
                output_field=DecimalField()
            ),
            last_meter_reading=Subquery(latest_meter_sq, output_field=DecimalField()),
            rent_amount=F('unit__rent_amount'),
        )
        .order_by('unit__unit_number')
    )

    # ---- Collect lease data ----
    leases_data = []
    for lease in leases_qs:
        rent_amount = lease.rent_amount or Decimal('0.00')
        total_paid = lease.total_paid or Decimal('0.00')
        if not isinstance(total_paid, Decimal):
            total_paid = Decimal(str(total_paid))
        balance = rent_amount - total_paid

        leases_data.append({
            'lease_obj': lease,
            'tenant': lease.tenant,
            'unit': lease.unit,
            'rent_amount': rent_amount,
            'total_paid': total_paid,
            'balance': balance,
            'current_meter': getattr(lease, 'last_meter_reading', None),
            'lease_start': lease.start_date,
            'lease_end': getattr(lease, 'end_date', None),
            'unleased': False,
        })

    # ---- Include unleased tenants (if model supports property FK) ----
    tenants_with_lease_ids = [r['tenant'].id for r in leases_data]
    unleased_tenants = Tenant.objects.none()
    try:
        Tenant._meta.get_field('property')  # only if Tenant has property FK
        unleased_tenants = Tenant.objects.filter(property=property_obj).exclude(id__in=tenants_with_lease_ids)
    except Exception:
        pass

    for tenant in unleased_tenants:
        leases_data.append({
            'lease_obj': None,
            'tenant': tenant,
            'unit': None,
            'rent_amount': None,
            'total_paid': Decimal('0.00'),
            'balance': None,
            'current_meter': None,
            'lease_start': None,
            'lease_end': None,
            'unleased': True,
        })

    # ---- Aggregates ----
    total_deposit = sum(
        (r['lease_obj'].deposit_amount or Decimal('0.00'))
        for r in leases_data if r.get('lease_obj')
    ) if leases_data else Decimal('0.00')
    total_paid = sum((r.get('total_paid') or Decimal('0.00')) for r in leases_data)
    total_balance = sum((r.get('balance') or Decimal('0.00')) for r in leases_data)

    aggregates = {
        'total_deposit': total_deposit,
        'total_paid': total_paid,
        'total_balance': total_balance,
    }

    return leases_data, aggregates
