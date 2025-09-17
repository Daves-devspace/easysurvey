# =================================================================
# apps/tenant_management/billing/invoice_builder.py
# =================================================================

import logging
from datetime import date
from decimal import Decimal
from django.db import transaction, IntegrityError
from django.db.models import Sum

from apps.tenant_management.models import (
    Invoice, InvoiceLine, Lease, MeterReading, Deposit
)
from apps.tenant_management.utils.money_helpers import quantize_money
from apps.tenant_management.utils.date_helpers import get_billing_period_for_date
from .exceptions import InvoiceGenerationError, DuplicateInvoiceError

logger = logging.getLogger(__name__)


class InvoiceBuilder:
    """Handles invoice creation and management."""
    
    @staticmethod
    @transaction.atomic
    def create_monthly_invoice(tenant, billing_period_start: date, billing_period_end: date):
        """Create a new invoice for specific billing period."""
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                # Check for existing invoice
                invoice = Invoice.objects.select_for_update().filter(
                    tenant=tenant,
                    billing_period_start=billing_period_start,
                    billing_period_end=billing_period_end
                ).first()
                
                if invoice:
                    return invoice

                # Create new invoice
                invoice = Invoice.objects.create(
                    tenant=tenant,
                    billing_period_start=billing_period_start,
                    billing_period_end=billing_period_end,
                    status=Invoice.STATUS_DRAFT
                )
                
                logger.info(f"Created invoice {invoice.pk} for tenant {tenant.full_name}")
                return invoice
                
            except IntegrityError:
                retry_count += 1
                if retry_count >= max_retries:
                    # Final attempt to get existing invoice
                    invoice = Invoice.objects.filter(
                        tenant=tenant,
                        billing_period_start=billing_period_start,
                        billing_period_end=billing_period_end
                    ).first()
                    if invoice:
                        return invoice
                    raise DuplicateInvoiceError(f"Could not create invoice for {tenant}")
                continue

    @staticmethod
    def get_or_create_monthly_invoice(tenant, billing_date: date):
        """Get or create invoice based on billing date and property's billing day."""
        billing_day = tenant.property.billing_day
        start_date, end_date = get_billing_period_for_date(billing_date, billing_day)
        return InvoiceBuilder.create_monthly_invoice(tenant, start_date, end_date)

    @staticmethod
    @transaction.atomic
    def add_rent_charge(invoice, lease):
        """Add rent charge to invoice."""
        rent_line, created = InvoiceLine.objects.get_or_create(
            invoice=invoice,
            lease=lease,
            line_type=InvoiceLine.LINE_RENT,
            defaults={
                "description": f"Monthly Rent ({invoice.billing_period_start:%b %Y})",
                "amount": quantize_money(lease.unit.rent_amount),
            }
        )

        if not created and rent_line.amount != lease.unit.rent_amount:
            rent_line.amount = quantize_money(lease.unit.rent_amount)
            rent_line.save(update_fields=["amount"])

        return rent_line

    @staticmethod
    @transaction.atomic
    def add_water_charges(invoice, lease, meter_readings):
        """Add water charges to invoice based on meter readings."""
        if not meter_readings:
            return None

        total_usage = sum_money_values([r.usage for r in meter_readings])
        total_amount = sum_money_values([r.amount for r in meter_readings])

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

        return line

    @staticmethod
    @transaction.atomic
    def add_security_deposit(invoice, lease):
        """Add security deposit to invoice (first-time only)."""
        from apps.tenant_management.utils.date_helpers import is_first_invoice_for_lease
        
        if not is_first_invoice_for_lease(lease, invoice.billing_period_start):
            return None

        if lease.deposit_amount <= 0:
            return None

        # Get or create deposit record
        deposit, _ = Deposit.objects.get_or_create(
            lease=lease,
            tenant=lease.tenant,
            defaults={
                "amount": lease.deposit_amount,
                "amount_held": Decimal('0.00')
            }
        )

        # Create deposit line
        deposit_line, created = InvoiceLine.objects.get_or_create(
            invoice=invoice,
            lease=lease,
            line_type=InvoiceLine.LINE_DEPOSIT,
            deposit=deposit,
            defaults={
                "description": f"Security Deposit ({lease.unit.unit_number})",
                "amount": quantize_money(lease.deposit_amount),
            }
        )

        return deposit_line

    @staticmethod
    @transaction.atomic
    def finalize_invoice(invoice):
        """Finalize invoice and update status."""
        # Recalculate total
        total = invoice.lines.aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
        invoice.total_amount = total
        
        # Update status based on content
        has_rent = invoice.lines.filter(line_type=InvoiceLine.LINE_RENT).exists()
        has_water = invoice.lines.filter(line_type=InvoiceLine.LINE_WATER).exists()

        if has_rent and has_water:
            invoice.status = Invoice.STATUS_FINALIZED
        elif has_rent:
            invoice.status = Invoice.STATUS_PENDING
        else:
            invoice.status = Invoice.STATUS_DRAFT

        invoice.save(update_fields=['total_amount', 'status'])
        
        logger.info(f"Finalized invoice {invoice.pk} with status {invoice.status}")
        return invoice