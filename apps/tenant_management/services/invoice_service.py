from django.db import transaction
from decimal import Decimal
from datetime import date
from apps.tenant_management.models import Invoice, InvoiceLine, Lease, MeterReading, Deposit
from apps.tenant_management.services.billing_service import BillingService
from apps.tenant_management.services import BaseService
from apps.tenant_management.helpers.date_helpers import get_billing_period_for_month
from apps.tenant_management.helpers.money_helpers import quantize_money as q
import logging

logger = logging.getLogger(__name__)

class InvoiceService(BaseService):
    """Service for handling invoice-related operations."""
    
    @classmethod
    def upsert_rent_invoice_line_for_lease(cls, lease: Lease, billing_date: date = None):
        """
        Generate Rent for the UPCOMING period (Rent Forward).
        If billing_date is Feb 1, this generates Feb Rent.
        """
        billing_date = billing_date or date.today()
        invoice = BillingService.get_or_create_monthly_invoice(lease.tenant, billing_date)

        # --- Rent Line ---
        # Description: "Monthly Rent (Feb 2025)"
        rent_desc = f"Monthly Rent ({invoice.billing_period_start:%b %Y})"
        
        rent_line, created = InvoiceLine.objects.get_or_create(
            invoice=invoice,
            lease=lease,
            line_type=InvoiceLine.LINE_RENT,
            defaults={
                "description": rent_desc,
                "amount": q(lease.unit.rent_amount),
            }
        )

        # Logic to update rent if it changed
        if not created and rent_line.amount != lease.unit.rent_amount:
            rent_line.amount = q(lease.unit.rent_amount)
            rent_line.save(update_fields=["amount"])

        # --- Deposit Line Logic (Kept your existing logic) ---
        cls._handle_deposit_line(lease, invoice)

        invoice.recalc_total()
        # Update status to PENDING (Rent is there, waiting for water?)
        invoice.update_status_for_lease(lease) 
        return invoice

    @classmethod
    def upsert_water_invoice_line_from_reading(cls, reading, billing_month_date=None):
        lease = Lease.objects.filter(unit=reading.unit, is_active=True).select_related('tenant').first()
        if not lease: return None

        target_date = billing_month_date or reading.reading_date
        invoice = BillingService.get_or_create_monthly_invoice(lease.tenant, target_date)

        # --- ROBUST CALCULATION ---
        # We trust the values stored on the reading object by the Signal
        usage = reading.usage or Decimal('0.00')
        rate = reading.rate_per_cubic_meter or Decimal('0.00')
        amount = q(usage * rate)

        usage_month_str = reading.reading_date.strftime('%b %Y')
        description = f"Water usage ({usage_month_str}) - {usage}m³"

        line, created = InvoiceLine.objects.get_or_create(
            invoice=invoice,
            lease=lease,
            line_type=InvoiceLine.LINE_WATER,
            meter_reading=reading,
            defaults={"description": description, "amount": amount}
        )

        if not created and line.amount != amount:
            line.amount = amount
            line.save(update_fields=["amount"])

        invoice.recalc_total()
        invoice.update_status_for_lease(lease)
        
        return line

    @classmethod
    def _handle_deposit_line(cls, lease, invoice):
        """Helper to keep upsert_rent clean"""
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