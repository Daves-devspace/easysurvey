# apps/tenant_management/billings/utils.py - FIXED VERSION

import logging
from django.utils import timezone
from decimal import Decimal
from datetime import date
from django.db import transaction, IntegrityError
from django.db.models import Sum, Exists, OuterRef

from apps.tenant_management.models import (
    Lease, Invoice, InvoiceLine, MeterReading
)

from apps.tenant_management.services import PaymentService

from .services import (
    get_or_create_monthly_invoice,
    upsert_rent_invoice_line_for_lease,
    billing_period_for_billing_month,
)

logger = logging.getLogger(__name__)


def _process_lease_for_month(lease: Lease, target_date: date):
    """
    Process a single lease for a specific month, handling rent, water, and deposits.
    Uses the property's billing day to determine the correct billing period.
    
    FIXED: Now automatically applies tenant credits after creating the invoice.
    
    Args:
        lease: The lease to process
        target_date: The target month (any day in the month)
        
    Returns:
        Dictionary with processing details and results
    """
    # Initialize with all possible keys to prevent KeyErrors
    lease_detail = {
        "lease_id": lease.pk,
        "tenant": lease.tenant.full_name,
        "unit": str(lease.unit),
        "status": "unknown",
        "error": None,
        "water_lines_added": 0,
        "water_usage": "0.00",
        "rent_added": False,
        "invoice_id": None,
        "invoice_status": None,
        "invoice_total": "0.00"
    }

    try:
        with transaction.atomic():
            # Get billing period for the target month
            billing_day = lease.tenant.property.billing_day
            start_of_period, end_of_period = billing_period_for_billing_month(
                target_date, billing_day
            )

            # Get or create invoice for this billing period
            invoice = get_or_create_monthly_invoice(lease.tenant, start_of_period)
            
            # Check if this is the first invoice for this specific lease
            has_existing_deposit = InvoiceLine.objects.filter(
                lease=lease,
                line_type=InvoiceLine.LINE_DEPOSIT
            ).exists()
            
            is_first_invoice = not has_existing_deposit

            # Process rent line (and deposit if first invoice)
            upsert_rent_invoice_line_for_lease(lease, billing_date=start_of_period, 
                                              is_first_invoice=is_first_invoice)
            lease_detail["rent_added"] = True

            # Process water usage lines
            water_lines_added = 0
            total_usage = Decimal('0.00')
            total_amount = Decimal('0.00')
            
            # Get readings in this billing period
            readings_in_period = MeterReading.objects.filter(
                unit=lease.unit,
                reading_date__range=(start_of_period, end_of_period),
                current_reading__isnull=False
            ).order_by("reading_date")

            if readings_in_period.exists():
                # Calculate total usage and amount from all readings in period
                total_usage = sum(reading.usage or Decimal('0') for reading in readings_in_period)
                total_amount = sum(reading.amount or Decimal('0') for reading in readings_in_period)
                
                # Create or update water line
                line, created = InvoiceLine.objects.get_or_create(
                    invoice=invoice,
                    lease=lease,
                    line_type=InvoiceLine.LINE_WATER,
                    defaults={
                        "description": f"Water usage ({invoice.billing_period_start:%b %Y})",
                        "amount": total_amount,
                    }
                )
                
                if not created:
                    line.amount = total_amount
                    line.description = f"Water usage ({invoice.billing_period_start:%b %Y})"
                    line.save(update_fields=["amount", "description"])
                
                water_lines_added = 1

            lease_detail["water_lines_added"] = water_lines_added
            lease_detail["water_usage"] = str(total_usage)

            # Update invoice status based on content
            invoice.refresh_from_db()
            has_rent = invoice.lines.filter(
                lease=lease, 
                line_type=InvoiceLine.LINE_RENT
            ).exists()
            has_water = invoice.lines.filter(
                lease=lease, 
                line_type=InvoiceLine.LINE_WATER
            ).exists()

            if has_rent and has_water:
                invoice.status = Invoice.STATUS_FINALIZED
            elif has_rent:
                invoice.status = Invoice.STATUS_PENDING
            else:
                invoice.status = Invoice.STATUS_DRAFT

            invoice.recalc_total()
            invoice.save(update_fields=["status", "total_amount"])

            # FIXED: Apply any available credits to the newly created/updated invoice
            # This ensures credits are automatically applied to new invoices
            try:
                credit_result = PaymentService.apply_credit_to_invoice(
                    tenant=lease.tenant,
                    payment_amount=None,  # Use existing tenant credits
                    reference="Auto-apply credits",
                    method="TenantBalance",
                    apply_to_deposit=False,
                    invoice=None,  # Let it apply to newest invoice (LIFO)
                    use_logger=False
                )
                
                # Log credit application if any credits were applied
                if Decimal(credit_result.get('applied_to_invoices', '0')) > 0:
                    logger.info(f"Applied {credit_result['applied_to_invoices']} in credits to invoices for lease {lease.pk}")
                    
            except Exception as e:
                logger.warning(f"Failed to apply credits for lease {lease.pk}: {e}")

            # Refresh invoice to get updated balance after credit application
            invoice.refresh_from_db()

            # Update result details
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
    Generate invoices for all active leases for a given month.
    
    FIXED: Now properly applies credits during the batch process.
    
    Args:
        target_date: The target month (defaults to current month)
        
    Returns:
        Dictionary with counts of processed leases and details
    """
    target_date = target_date or timezone.now().date()
    billing_month = target_date.replace(day=1)  # Use first day for consistency
    
    logger.info(f"Starting batch invoice generation for billing month: {billing_month}")

    leases = list(Lease.objects.filter(is_active=True).select_related("tenant", "unit__property"))

    if not leases:
        logger.info("No active leases found")
        return {"created": 0, "skipped": 0, "errors": 0, "details": []}

    created_count = 0
    skipped_count = 0
    error_count = 0
    details = []

    for lease in leases:
        lease_detail = _process_lease_for_month(lease, billing_month)
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
    
    FIXED: Now includes automatic credit application.
    """
    billing_date = billing_date or timezone.now().date()
    # Ensure we use first day of month for consistency
    billing_month = billing_date.replace(day=1)
    return _process_lease_for_month(lease, billing_month)