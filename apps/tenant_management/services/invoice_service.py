from django.db import transaction
from decimal import Decimal
from datetime import date
from apps.tenant_management.models import Invoice, InvoiceLine, Lease, MeterReading, Deposit, Property
from apps.tenant_management.services.billing_service import BillingService
from apps.tenant_management.services import BaseService
from apps.tenant_management.helpers.money_helpers import quantize_money as q
import logging

logger = logging.getLogger(__name__)

class InvoiceService(BaseService):
    """Service for handling invoice-related operations."""
    
    @classmethod
    def upsert_rent_invoice_line_for_lease(cls, lease: Lease, billing_date: date = None):
        """Generate Rent for the UPCOMING period (Rent Forward)."""
        billing_date = billing_date or date.today()
        invoice = BillingService.get_or_create_monthly_invoice(lease.tenant, billing_date, lease=lease)

        rent_desc = f"Monthly Rent ({invoice.billing_period_start:%b %Y})"
        
        # Rent is unique per invoice/lease
        rent_line, created = InvoiceLine.objects.get_or_create(
            invoice=invoice,
            lease=lease,
            line_type=InvoiceLine.LINE_RENT,
            defaults={"description": rent_desc, "amount": q(lease.unit.rent_amount)}
        )

        if not created and rent_line.amount != lease.unit.rent_amount:
            rent_line.amount = q(lease.unit.rent_amount)
            rent_line.save(update_fields=["amount"])

        cls._handle_deposit_line(lease, invoice)
        invoice.recalc_total()
        invoice.update_status_for_lease(lease) 
        return invoice

    @classmethod
    def upsert_water_invoice_line_from_reading(cls, reading, billing_month_date=None):
        """
        Updates the Invoice with Water Charges.
        ENFORCES: Single Water Line per Invoice (Overwrites if multiple readings exist).
        """
        lease = Lease.objects.filter(unit=reading.unit, is_active=True).select_related('tenant', 'unit__property').first()
        if not lease: return None

        water_policy = lease.unit.property.water_policy
        if water_policy == Property.PREPAID:
            return None

        target_date = billing_month_date or reading.reading_date
        invoice = BillingService.get_or_create_monthly_invoice(lease.tenant, target_date, lease=lease)

        # Calculate Amounts
        usage = reading.usage or Decimal('0.00')
        rate = reading.rate_per_cubic_meter or Decimal('0.00')
        amount = q(usage * rate)
        
        usage_month_str = reading.reading_date.strftime('%b %Y')
        description = f"Water usage ({usage_month_str}) - {usage}m³"

        # --- FIX: Prevent Duplicate Lines ---
        # Instead of get_or_create(meter_reading=reading), we look for ANY water line
        # on this invoice for this lease.
        line = InvoiceLine.objects.filter(
            invoice=invoice,
            lease=lease,
            line_type=InvoiceLine.LINE_WATER
        ).first()

        if line:
            # Update existing line with NEW reading data
            line.meter_reading = reading
            line.description = description
            line.amount = amount
            line.save()
        else:
            # Create new line
            line = InvoiceLine.objects.create(
                invoice=invoice,
                lease=lease,
                line_type=InvoiceLine.LINE_WATER,
                meter_reading=reading,
                description=description,
                amount=amount
            )

        invoice.recalc_total()
        invoice.update_status_for_lease(lease)
        return line

    @classmethod
    def _handle_deposit_line(cls, lease, invoice):
        has_existing_deposit = InvoiceLine.objects.filter(lease=lease, line_type=InvoiceLine.LINE_DEPOSIT).exists()
        if not has_existing_deposit and lease.deposit_amount > 0:
            deposit, _ = Deposit.objects.get_or_create(
                lease=lease, tenant=lease.tenant,
                defaults={"amount": lease.deposit_amount, "amount_held": Decimal('0.00')}
            )
            InvoiceLine.objects.get_or_create(
                invoice=invoice, lease=lease, line_type=InvoiceLine.LINE_DEPOSIT, deposit=deposit,
                defaults={"description": f"Security Deposit ({lease.unit.unit_number})", "amount": q(lease.deposit_amount)}
            )