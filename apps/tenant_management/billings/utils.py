# Updated batch processing functions
import logging
from django.utils import timezone
from decimal import Decimal
from datetime import date
from django.db import transaction, IntegrityError
from django.db.models import Prefetch

from apps.tenant_management.models import (
    Lease, Invoice, InvoiceLine, MeterReading
)

from .services import (
    get_or_create_monthly_invoice,
    upsert_rent_invoice_line_for_lease,
    upsert_water_invoice_line_from_reading,
    apply_credit_and_deposit,
    month_bounds_for,
    billing_period_for_billing_month,  # ← Import the correct function
)

logger = logging.getLogger(__name__)


def _process_lease_for_month(lease: Lease, target_date: date):
    """
    Internal helper to process a single lease for a specific month.
    Safe, idempotent, handles deposits, rent, water, and credits.
    
    Returns: dict with lease processing details
    """
    lease_detail = {
        "lease_id": lease.pk,
        "tenant": lease.tenant.full_name,
        "unit": str(lease.unit),
        "status": "unknown",
        "error": None
    }

    # FIXED: Use billing period bounds instead of calendar month bounds
    billing_day = lease.tenant.property.billing_day
    start_of_period, end_of_period = billing_period_for_billing_month(target_date, billing_day)

    try:
        with transaction.atomic():
            # Canonical invoice - this should create invoice for the correct billing period
            invoice = get_or_create_monthly_invoice(lease.tenant, target_date)

            # Prevent duplicate rent/deposit lines
            existing_rent_deposit = invoice.lines.filter(
                lease=lease,
                line_type__in=[InvoiceLine.LINE_RENT, InvoiceLine.LINE_DEPOSIT]
            ).exists()

            if existing_rent_deposit:
                lease_detail["status"] = "skipped"
                lease_detail["reason"] = f"Invoice {invoice.pk} already has lines for this lease"
                lease_detail["invoice_id"] = invoice.pk
                return lease_detail

            # --- Rent (and deposit if first invoice) ---
            upsert_rent_invoice_line_for_lease(
                lease,
                billing_date=target_date,
            )
            lease_detail["rent_added"] = True

            # --- Water lines ---
            water_lines_added = 0
            prefetched_readings = getattr(lease.unit, "prefetched_meter_readings", None)
            if prefetched_readings is None:
                # FIXED: Filter by billing period, not calendar month
                # AND only include readings where user selected this billing month
                prefetched_readings = MeterReading.objects.filter(
                    unit=lease.unit,
                    # The key insight: reading_date should be the user-selected billing month (always day 1)
                    reading_date=target_date.replace(day=1),  # ← Only exact match with billing month
                    current_reading__isnull=False
                ).order_by("reading_date")

            for reading in prefetched_readings:
                try:
                    # Pass the billing month date to ensure correct billing period
                    line = upsert_water_invoice_line_from_reading(
                        reading, 
                        billing_month_date=target_date
                    )
                    if line:
                        water_lines_added += 1
                except Exception as e:
                    logger.warning(f"Water line failed for reading {reading.pk}: {e}")
                    continue
            
            lease_detail["water_lines_added"] = water_lines_added

            # --- Apply credits ---
            try:
                apply_credit_and_deposit(
                    tenant=lease.tenant,
                    payment_amount=None,
                    reference="Auto-apply",
                    method="TenantBalance",
                    apply_to_deposit=False,
                    invoice=invoice,
                    use_logger=False
                )
            except Exception as e:
                logger.warning(f"Failed to apply credits for lease {lease.pk}: {e}")

            # --- Finalize invoice status ---
            invoice.refresh_from_db()
            has_rent = invoice.lines.filter(lease=lease, line_type=InvoiceLine.LINE_RENT).exists()
            has_water = invoice.lines.filter(lease=lease, line_type=InvoiceLine.LINE_WATER, meter_reading__isnull=False).exists()
            has_deposit = invoice.lines.filter(lease=lease, line_type=InvoiceLine.LINE_DEPOSIT).exists()
            water_expected = len(prefetched_readings) > 0

            if has_rent and (has_water or not water_expected):
                invoice.status = Invoice.STATUS_FINALIZED
            elif has_rent or has_deposit:
                invoice.status = Invoice.STATUS_PENDING
            else:
                invoice.status = Invoice.STATUS_DRAFT

            invoice.recalc_total()
            invoice.save(update_fields=["status", "total_amount"])

            lease_detail.update({
                "status": "created",
                "invoice_id": invoice.pk,
                "invoice_status": invoice.status,
                "invoice_total": str(invoice.total_amount)
            })

    except Exception as e:
        lease_detail.update({"status": "error", "error": str(e)})
        logger.error(f"Failed to process lease {lease.pk}: {e}", exc_info=True)

    return lease_detail


def generate_monthly_invoices_for_all_leases(target_date: date = None):
    """
    Batch job to generate monthly invoices for all active leases.
    Now properly handles billing periods vs calendar months.

    Returns: dict with counts and details per lease
    """
    target_date = target_date or timezone.now().date()
    # Ensure target_date is always first day of month for consistency
    billing_month = target_date.replace(day=1)
    
    logger.info(f"Starting batch invoice generation for billing month: {billing_month}")

    # FIXED: Prefetch readings by exact billing month match, not date ranges
    leases_qs = (
        Lease.objects.filter(is_active=True)
        .select_related("tenant", "unit__property")
        .prefetch_related(
            Prefetch(
                "unit__meter_readings",
                queryset=MeterReading.objects.filter(
                    # Only readings where user selected this exact billing month
                    reading_date=billing_month,  # ← Exact match, not range
                    current_reading__isnull=False
                ).order_by("reading_date"),
                to_attr="prefetched_meter_readings"
            )
        )
    )
    leases = list(leases_qs)

    if not leases:
        logger.info("No active leases found")
        return {"created": 0, "skipped": 0, "errors": 0, "details": []}

    created_count = 0
    skipped_count = 0
    error_count = 0
    details = []

    for lease in leases:
        lease_detail = _process_lease_for_month(lease, billing_month)

        # Update counts based on lease status
        status = lease_detail.get("status")
        if status == "created":
            created_count += 1
        elif status == "skipped":
            skipped_count += 1
        elif status == "error":
            error_count += 1

        details.append(lease_detail)

    result = {
        "created": created_count,
        "skipped": skipped_count,
        "errors": error_count,
        "total_processed": len(leases),
        "details": details
    }

    logger.info(
        f"Batch invoice generation complete: "
        f"{created_count} created, {skipped_count} skipped, {error_count} errors"
    )

    return result


def generate_monthly_invoice_for_lease(lease: Lease, billing_date=None):
    """
    Public single-lease wrapper for generating a monthly invoice.
    """
    billing_date = billing_date or timezone.now().date()
    # Ensure we use first day of month for consistency
    billing_month = billing_date.replace(day=1)
    return _process_lease_for_month(lease, billing_month)