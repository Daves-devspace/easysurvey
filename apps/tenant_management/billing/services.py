# Import the new services
from apps.tenant_management.services.billing_service import BillingService
from apps.tenant_management.services.invoice_service import InvoiceService
from apps.tenant_management.services.payment_service import PaymentService
from apps.tenant_management.services.deposit_service import DepositService
from apps.tenant_management.utils.date_utils import month_bounds_for, previous_month_bounds
from apps.tenant_management.utils.billing_utils import billing_period_for_billing_month, billing_period_for_reading_date
from apps.tenant_management.utils.payment_utils import q
from apps.tenant_management.utils.logging_utils import get_logger
from apps.tenant_management.models import (
    Lease, Invoice, InvoiceLine, MeterReading, WaterRate
)

from datetime import date as _date

from django.db.models import F, Q, Sum, Exists, OuterRef
from django.db.models import Q
from functools import lru_cache 
from decimal import Decimal
import logging
logger = logging.getLogger(__name__)



# Re-export functions for backward compatibility
get_or_create_monthly_invoice = BillingService.get_or_create_monthly_invoice
get_or_create_invoice_for_period = BillingService.get_or_create_invoice_for_period
upsert_rent_invoice_line_for_lease = InvoiceService.upsert_rent_invoice_line_for_lease
upsert_water_invoice_line_from_reading = InvoiceService.upsert_water_invoice_line_from_reading
apply_credit_and_deposit = PaymentService.process_payment
apply_deposit_to_invoice = DepositService.apply_deposit_to_invoice
refund_deposit = DepositService.refund_deposit

# Remove the old complex functions but keep utility functions that are still needed

# -------------------------
# KEEP ONLY THESE UTILITY FUNCTIONS
# -------------------------

@lru_cache(maxsize=1024)
def _cached_rate_lookup(water_company_id, on_date_iso):
    """
    Internal cached lookup. Returns WaterRate.id or None.
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
        return WaterRate.objects.get(pk=rate_id)
    return None

def get_active_lease_for_unit(unit):
    return (
        Lease.objects.filter(unit=unit, is_active=True)
        .order_by('-start_date')
        .select_related('tenant', 'unit')
        .first()
    )

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

# -------------------------
# REMOVE ALL OTHER FUNCTIONS
# -------------------------
# The following functions have been moved to appropriate services:
# - allocate_payment_to_deposit_lines (moved to PaymentService)
# - apply_tenant_credits_to_invoice (moved to PaymentService)
# - The massive apply_credit_and_deposit function (moved to PaymentService)