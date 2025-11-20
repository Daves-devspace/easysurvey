# dashboard/views.py
from django.utils.timezone import now
from django.db.models import Sum, Count, Avg, F, Q, ExpressionWrapper, DurationField
from datetime import timedelta
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.tenant_management.models import TenantBalance, Tenant
from apps.tenant_management.models import Invoice, InvoiceLine, Payment, Deposit
from apps.tenant_management.models import Unit, Property
from apps.tenant_management.models import MeterReading


@api_view(["GET"])
def financial_kpis(request):
    """Returns totals: revenue, outstanding, vacancy loss, deposits"""
    total_collected = Payment.objects.aggregate(total=Sum("amount"))["total"] or 0
    outstanding = sum(inv.balance for inv in Invoice.objects.filter(is_paid=False))
    vacant_loss = Unit.objects.filter(is_occupied=False).aggregate(
        total=Sum("rent_amount")
    )["total"] or 0
    deposits_held = Deposit.objects.aggregate(total=Sum("amount_held"))["total"] or 0

    return Response({
        "total_collected": total_collected,
        "outstanding": outstanding,
        "vacant_loss": vacant_loss,
        "deposits_held": deposits_held,
    })


@api_view(["GET"])
def occupancy_kpis(request):
    """Returns occupancy rate, vacancy, avg rent, per property stats"""
    total_units = Unit.objects.count()
    occupied_units = Unit.objects.filter(is_occupied=True).count()
    occupancy_rate = (occupied_units / total_units * 100) if total_units else 0

    avg_rent = Unit.objects.aggregate(avg=Avg("rent_amount"))["avg"] or 0

    occupancy_per_property = (
        Unit.objects.values("property__name")
        .annotate(
            total_units=Count("id"),
            occupied_units=Count("id", filter=Q(is_occupied=True))
        )
        .annotate(occupancy_rate=F("occupied_units") * 100.0 / F("total_units"))
    )

    return Response({
        "occupancy_rate": occupancy_rate,
        "vacancy_rate": 100 - occupancy_rate,
        "avg_rent": avg_rent,
        "per_property": list(occupancy_per_property),
    })


@api_view(["GET"])
def operational_kpis(request):
    """Invoice breakdown, avg time to finalize, utility recovery"""
    invoice_status = (
        Invoice.objects.values("status")
        .annotate(count=Count("id"))
    )

    avg_finalization_time = (
        Invoice.objects.filter(status=Invoice.STATUS_FINALIZED)
        .annotate(
            finalization_delay=ExpressionWrapper(
                F("created_at") - F("billing_period_end"),
                output_field=DurationField()
            )
        )
        .aggregate(avg_delay=Avg("finalization_delay"))
    )["avg_delay"]

    water_billed = InvoiceLine.objects.filter(line_type=InvoiceLine.LINE_WATER).aggregate(
        total=Sum("amount")
    )["total"] or 0

    water_usage_cost = MeterReading.objects.aggregate(
        total=Sum("amount")
    )["total"] or 0

    utility_recovery_rate = (water_billed / water_usage_cost * 100) if water_usage_cost else 0

    return Response({
        "invoice_status": list(invoice_status),
        "avg_finalization_time": avg_finalization_time,
        "utility_recovery_rate": utility_recovery_rate,
    })


@api_view(["GET"])
def collections_kpis(request):
    """Collection rate, aging, top arrears tenants"""
    today = now().date()

    collected = Payment.objects.aggregate(total=Sum("amount"))["total"] or 0
    billed = Invoice.objects.aggregate(total=Sum("total_amount"))["total"] or 0
    collection_rate = (collected / billed * 100) if billed else 0

    aging = {
        "0-30": Invoice.objects.filter(
            is_paid=False,
            billing_period_end__gte=today - timedelta(days=30)
        ).aggregate(total=Sum("total_amount"))["total"] or 0,

        "30-60": Invoice.objects.filter(
            is_paid=False,
            billing_period_end__lt=today - timedelta(days=30),
            billing_period_end__gte=today - timedelta(days=60)
        ).aggregate(total=Sum("total_amount"))["total"] or 0,

        "60+": Invoice.objects.filter(
            is_paid=False,
            billing_period_end__lt=today - timedelta(days=60)
        ).aggregate(total=Sum("total_amount"))["total"] or 0,
    }

    top_arrears = (
        TenantBalance.objects.filter(balance__gt=0)
        .order_by("-balance")[:5]
        .values("tenant__full_name", "balance")
    )

    return Response({
        "collection_rate": collection_rate,
        "aging": aging,
        "top_arrears": list(top_arrears),
    })
