from django.db import transaction
from decimal import Decimal
from datetime import date
from apps.tenant_management.models import Invoice, InvoiceLine, Lease, MeterReading, Deposit
from apps.tenant_management.services.billing_service import BillingService
from apps.tenant_management.utils.payment_utils import q
from apps.tenant_management.services import BaseService
from apps.tenant_management.exceptions import InvoiceGenerationError
from apps.tenant_management.utils.billing_utils import billing_period_for_billing_month
import logging
from django.db.models import Q

logger = logging.getLogger(__name__)

class InvoiceService(BaseService):
    """Service for handling invoice-related operations."""
    
    @classmethod
    def upsert_rent_invoice_line_for_lease(cls, lease: Lease, billing_date: date = None, is_first_invoice: bool = False):
        """
        Create or update rent & deposit lines for the lease respecting billing_day.
        """
        billing_date = billing_date or date.today()
        invoice = BillingService.get_or_create_monthly_invoice(lease.tenant, billing_date)

        # --- Rent Line ---
        rent_line, created = InvoiceLine.objects.get_or_create(
            invoice=invoice,
            lease=lease,
            line_type=InvoiceLine.LINE_RENT,
            defaults={
                "description": f"Monthly Rent ({invoice.billing_period_start:%b %Y})",
                "amount": q(lease.unit.rent_amount),
            }
        )

        if not created and rent_line.amount != lease.unit.rent_amount:
            rent_line.amount = q(lease.unit.rent_amount)
            rent_line.save(update_fields=["amount"])

        # --- Deposit Line (first invoice only when explicitly indicated) ---
        # Check if this is the first invoice for this specific lease
        has_existing_deposit = InvoiceLine.objects.filter(
            lease=lease,
            line_type=InvoiceLine.LINE_DEPOSIT
        ).exists()
        
        is_first_invoice_for_lease = not has_existing_deposit

        if is_first_invoice_for_lease and lease.deposit_amount > 0:
            deposit, _ = Deposit.objects.get_or_create(
                lease=lease,
                tenant=lease.tenant,
                defaults={"amount": lease.deposit_amount, "amount_held": Decimal('0.00')}
            )
            
            # Create the deposit line but DON'T create a ledger entry here
            InvoiceLine.objects.get_or_create(
                invoice=invoice,
                lease=lease,
                line_type=InvoiceLine.LINE_DEPOSIT,
                deposit=deposit,
                defaults={
                    "description": f"Security Deposit ({lease.unit.unit_number})",
                    "amount": q(lease.deposit_amount),
                }
            )

        invoice.recalc_total()
        return invoice
    
    @classmethod
    def upsert_water_invoice_line_from_reading(cls, reading, billing_month_date=None):
        """
        Create or update water usage line for a unit's lease.
        """
        lease = Lease.objects.filter(unit=reading.unit, is_active=True).select_related('tenant').first()
        if not lease:
            return None

        # Use the user-selected billing month if provided
        if billing_month_date:
            billing_day = lease.tenant.property.billing_day
            start, end = billing_period_for_billing_month(billing_month_date, billing_day)
            invoice = BillingService.get_or_create_invoice_for_period(lease.tenant, start, end)
        else:
            # Fallback to reading date logic
            billing_date = reading.reading_date
            invoice = BillingService.get_or_create_monthly_invoice(lease.tenant, billing_date)

        amount = q((reading.usage or 0) * (reading.rate_per_cubic_meter or 0))

        line, created = InvoiceLine.objects.get_or_create(
            invoice=invoice,
            lease=lease,
            line_type=InvoiceLine.LINE_WATER,
            meter_reading=reading,
            defaults={
                "description": f"Water usage ({invoice.billing_period_start:%b %Y})",
                "amount": amount,
            }
        )

        if not created and line.amount != amount:
            line.amount = amount
            line.save(update_fields=["amount"])

        invoice.recalc_total()
        return line