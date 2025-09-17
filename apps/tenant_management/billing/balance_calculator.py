# =================================================================
# apps/tenant_management/billing/balance_calculator.py
# =================================================================

import logging
from decimal import Decimal
from django.db.models import Sum

from apps.tenant_management.models import Invoice, Payment, TenantBalance
from apps.tenant_management.utils.money_helpers import quantize_money

logger = logging.getLogger(__name__)


class BalanceCalculator:
    """Handles balance calculations and status updates."""
    
    @staticmethod
    def calculate_tenant_balance(tenant):
        """
        Calculate tenant's current balance.
        Positive = Tenant owes money
        Negative = Tenant has credit
        """
        # Sum of unpaid invoice balances
        unpaid_invoices = Invoice.objects.filter(tenant=tenant, is_paid=False)
        total_owed = sum(quantize_money(inv.balance) for inv in unpaid_invoices)
        
        # Sum of unallocated credits
        unallocated_credits = Payment.objects.filter(
            tenant=tenant,
            invoice__isnull=True
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        balance = quantize_money(total_owed - unallocated_credits)
        
        logger.debug(f"Tenant {tenant.full_name}: owed={total_owed}, credits={unallocated_credits}, balance={balance}")
        return balance

    @staticmethod
    def update_tenant_balance(tenant):
        """Update the tenant's balance record."""
        balance = BalanceCalculator.calculate_tenant_balance(tenant)
        
        tenant_balance, created = TenantBalance.objects.get_or_create(
            tenant=tenant,
            defaults={'balance': balance}
        )
        
        if not created:
            tenant_balance.balance = balance
            tenant_balance.save(update_fields=['balance'])
        
        logger.info(f"Updated balance for {tenant.full_name}: {balance}")
        return tenant_balance

    @staticmethod
    def update_invoice_status(invoice):
        """Update invoice payment status based on payments."""
        balance = quantize_money(invoice.balance)
        
        if balance <= 0 and not invoice.is_paid:
            invoice.mark_paid()
            logger.info(f"Invoice {invoice.pk} marked as paid")
        elif balance > 0 and invoice.is_paid:
            invoice.is_paid = False
            invoice.save(update_fields=['is_paid'])
            logger.info(f"Invoice {invoice.pk} marked as unpaid")

    @staticmethod
    def get_tenant_credits(tenant):
        """Get available tenant credits."""
        unallocated_payments = Payment.objects.filter(
            tenant=tenant,
            invoice__isnull=True
        )
        return sum(quantize_money(p.amount) for p in unallocated_payments)

    @staticmethod
    def get_outstanding_invoices(tenant):
        """Get all outstanding invoices for tenant."""
        return Invoice.objects.filter(
            tenant=tenant,
            is_paid=False
        ).order_by('billing_period_start')

    @staticmethod
    def mark_invoice_as_paid(invoice):
        """Mark invoice as paid and update related records."""
        if not invoice.is_paid:
            invoice.mark_paid()
            BalanceCalculator.update_tenant_balance(invoice.tenant)