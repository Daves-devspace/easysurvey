from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from decimal import Decimal
from apps.tenant_management.models import Deposit, Payment, Invoice, InvoiceLine, LedgerEntry

def get_tenant_financials(tenant):
    """
    Returns tenant-level aggregates, properly separating deposits from credits
    """
    # Total deposit amount across all leases
    total_deposit = (
        Deposit.objects
        .filter(lease__tenant=tenant)
        .aggregate(
            total=Coalesce(Sum("amount"), Value(Decimal("0.00")), output_field=DecimalField())
        )["total"]
    ) or Decimal("0.00")

    # Total payments (excluding deposit portions)
    total_paid = (
        Payment.objects
        .filter(invoice__tenant=tenant)
        .aggregate(
            total=Coalesce(Sum("amount"), Value(Decimal("0.00")), output_field=DecimalField())
        )["total"]
    ) or Decimal("0.00")

    # Total invoiced (including deposits)
    total_invoiced = (
        Invoice.objects
        .filter(tenant=tenant)
        .aggregate(
            total=Coalesce(Sum("total_amount"), Value(Decimal("0.00")), output_field=DecimalField())
        )["total"]
    ) or Decimal("0.00")

    # Total rent (excluding deposits)
    total_rent = (
        InvoiceLine.objects
        .filter(invoice__tenant=tenant, line_type=InvoiceLine.LINE_RENT)
        .aggregate(
            total=Coalesce(Sum("amount"), Value(Decimal("0.00")), output_field=DecimalField())
        )["total"]
    ) or Decimal("0.00")

    # Total water charges
    total_water_charges = (
        InvoiceLine.objects
        .filter(invoice__tenant=tenant, meter_reading__isnull=False)
        .aggregate(
            total=Coalesce(Sum("amount"), Value(Decimal("0.00")), output_field=DecimalField())
        )["total"]
    ) or Decimal("0.00")

    # Calculate REAL tenant credit (excluding deposits)
    tenant_credit = LedgerEntry.objects.filter(
        tenant=tenant,
        invoice__isnull=True,
        deposit__isnull=True,  # Exclude deposit-related entries
        credit__gt=0
    ).aggregate(
        total=Coalesce(Sum("credit"), Value(Decimal("0.00")), output_field=DecimalField())
    )["total"] or Decimal("0.00")

    # Calculate balance based on rent + water only, excluding deposits
    rent_balance = (total_rent + total_water_charges) - (total_paid - total_deposit)

    return {
        "total_deposit": total_deposit,
        "total_paid": total_paid,
        "total_invoiced": total_invoiced,
        "total_rent": total_rent,
        "total_water_charges": total_water_charges,
        "total_balance": rent_balance,
        "tenant_credit": tenant_credit,  # Real credit, not including deposits
    }