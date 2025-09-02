from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from decimal import Decimal
from apps.tenant_management.models import Deposit, Payment, Invoice, InvoiceLine


def get_tenant_financials(tenant):
    """
    Returns tenant-level aggregates:
      - total_deposit
      - total_paid
      - total_invoiced
      - total_water_charges
      - total_balance
    Invoice-driven so rent + water + extras are all included.
    """
    total_deposit = (
        Deposit.objects
        .filter(lease__tenant=tenant)
        .aggregate(
            total=Coalesce(Sum("amount"), Value(Decimal("0.00")), output_field=DecimalField())
        )["total"]
    ) or Decimal("0.00")

    total_paid = (
        Payment.objects
        .filter(invoice__tenant=tenant)
        .aggregate(
            total=Coalesce(Sum("amount"), Value(Decimal("0.00")), output_field=DecimalField())
        )["total"]
    ) or Decimal("0.00")

    total_invoiced = (
        Invoice.objects
        .filter(tenant=tenant)
        .aggregate(
            total=Coalesce(Sum("total_amount"), Value(Decimal("0.00")), output_field=DecimalField())
        )["total"]
    ) or Decimal("0.00")

    total_water_charges = (
        InvoiceLine.objects
        .filter(invoice__tenant=tenant, meter_reading__isnull=False)
        .aggregate(
            total=Coalesce(Sum("amount"), Value(Decimal("0.00")), output_field=DecimalField())
        )["total"]
    ) or Decimal("0.00")

    return {
        "total_deposit": total_deposit,
        "total_paid": total_paid,
        "total_invoiced": total_invoiced,
        "total_water_charges": total_water_charges,
        "total_balance": total_invoiced - total_paid,
    }
