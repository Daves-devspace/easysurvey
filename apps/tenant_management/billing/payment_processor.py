# =================================================================
# apps/tenant_management/billing/payment_processor.py  
# =================================================================

import logging
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from django.db.models import Sum

from apps.tenant_management.models import (
    Payment, Invoice, InvoiceLine, Deposit, LedgerEntry, Lease
)
from apps.tenant_management.utils.money_helpers import quantize_money
from .exceptions import PaymentProcessingError, InvalidTenantError

logger = logging.getLogger(__name__)


class PaymentProcessor:
    """Handles payment processing and allocation."""
    
    @staticmethod
    @transaction.atomic
    def record_payment(tenant, amount: Decimal, method: str, reference: str = None):
        """Record a new payment."""
        if not tenant or not hasattr(tenant, 'pk'):
            raise InvalidTenantError("Invalid tenant provided")
            
        payment_amount = quantize_money(amount)
        if payment_amount <= 0:
            raise PaymentProcessingError("Payment amount must be greater than zero")

        payment = Payment.objects.create(
            tenant=tenant,
            amount=payment_amount,
            method=method,
            reference=reference or f"Payment {timezone.now().strftime('%Y%m%d%H%M%S')}"
        )
        
        logger.info(f"Recorded payment {payment.pk}: {payment_amount} for {tenant.full_name}")
        return payment

    @staticmethod
    @transaction.atomic
    def allocate_payment_to_deposits(tenant, available_amount: Decimal):
        """
        Allocate payment to outstanding deposits first.
        Returns (allocated_amount, remaining_amount).
        """
        if available_amount <= 0:
            return Decimal('0.00'), available_amount

        allocated_total = Decimal('0.00')
        remaining = quantize_money(available_amount)

        # Get unpaid deposit lines across all invoices (oldest first)
        unpaid_invoices = Invoice.objects.filter(
            tenant=tenant,
            is_paid=False
        ).order_by('billing_period_start', 'id')

        for invoice in unpaid_invoices:
            if remaining <= 0:
                break

            deposit_lines = invoice.lines.filter(
                line_type=InvoiceLine.LINE_DEPOSIT
            ).select_related('deposit')

            for line in deposit_lines:
                if remaining <= 0:
                    break

                deposit = line.deposit
                if not deposit or deposit.paid_at:
                    continue  # Skip if already paid

                # Calculate how much this deposit needs
                needed = quantize_money(deposit.amount - deposit.amount_held)
                if needed <= 0:
                    continue

                # Allocate payment to this deposit
                to_allocate = min(remaining, needed)
                
                deposit.amount_held = quantize_money(deposit.amount_held + to_allocate)
                if deposit.amount_held >= deposit.amount:
                    deposit.paid_at = timezone.now()
                
                deposit.save(update_fields=['amount_held', 'paid_at'])
                
                allocated_total = quantize_money(allocated_total + to_allocate)
                remaining = quantize_money(remaining - to_allocate)

                logger.info(f"Allocated {to_allocate} to deposit {deposit.pk}")

        return allocated_total, remaining

    @staticmethod
    @transaction.atomic
    def allocate_payment_to_invoices(tenant, available_amount: Decimal):
        """
        Allocate payment to invoices (oldest first).
        Returns (allocated_amount, remaining_amount).
        """
        if available_amount <= 0:
            return Decimal('0.00'), available_amount

        allocated_total = Decimal('0.00')
        remaining = quantize_money(available_amount)

        # Get unpaid invoices (oldest first)
        unpaid_invoices = Invoice.objects.filter(
            tenant=tenant,
            is_paid=False
        ).order_by('billing_period_start', 'id')

        for invoice in unpaid_invoices:
            if remaining <= 0:
                break

            invoice_balance = quantize_money(invoice.balance)
            if invoice_balance <= 0:
                continue

            # Allocate to this invoice
            to_allocate = min(remaining, invoice_balance)
            
            # Create payment record linked to this invoice
            Payment.objects.create(
                tenant=tenant,
                invoice=invoice,
                amount=to_allocate,
                method="Allocation",
                reference=f"Allocation to Invoice {invoice.pk}"
            )

            allocated_total = quantize_money(allocated_total + to_allocate)
            remaining = quantize_money(remaining - to_allocate)

            # Check if invoice is now paid
            invoice.refresh_from_db()
            if invoice.balance <= 0:
                invoice.mark_paid()
                logger.info(f"Invoice {invoice.pk} marked as paid")

        return allocated_total, remaining

    @staticmethod
    @transaction.atomic
    def store_overpayment_as_credit(tenant, amount: Decimal, original_payment):
        """Store overpayment as tenant credit for future use."""
        if amount <= 0:
            return None

        credit_amount = quantize_money(amount)
        
        credit_payment = Payment.objects.create(
            tenant=tenant,
            invoice=None,  # Unallocated credit
            amount=credit_amount,
            method=original_payment.method,
            reference=f"Credit from payment {original_payment.pk}"
        )

        logger.info(f"Stored {credit_amount} as credit for {tenant.full_name}")
        return credit_payment

    @staticmethod
    @transaction.atomic
    def apply_tenant_credits_to_new_invoice(invoice):
        """Apply existing tenant credits to a new invoice."""
        tenant = invoice.tenant
        
        # Find unallocated payments (credits)
        unallocated_payments = Payment.objects.filter(
            tenant=tenant,
            invoice__isnull=True
        ).order_by('payment_date')  # FIFO

        if not unallocated_payments.exists():
            return Decimal('0.00')

        available_credit = sum(quantize_money(p.amount) for p in unallocated_payments)
        invoice_balance = quantize_money(invoice.balance)
        
        if available_credit <= 0 or invoice_balance <= 0:
            return Decimal('0.00')

        # Apply credits to this invoice
        to_apply = min(available_credit, invoice_balance)
        remaining_to_apply = to_apply

        for payment in unallocated_payments:
            if remaining_to_apply <= 0:
                break

            payment_amount = quantize_money(payment.amount)
            if payment_amount <= 0:
                continue

            if payment_amount <= remaining_to_apply:
                # Use entire payment
                payment.invoice = invoice
                payment.reference = f"Credit applied to Invoice {invoice.pk}"
                payment.save(update_fields=['invoice', 'reference'])
                remaining_to_apply = quantize_money(remaining_to_apply - payment_amount)
            else:
                # Use partial payment
                Payment.objects.create(
                    tenant=tenant,
                    invoice=invoice,
                    amount=remaining_to_apply,
                    method=payment.method,
                    reference=f"Partial credit applied to Invoice {invoice.pk}",
                    payment_date=payment.payment_date
                )
                
                # Reduce original payment
                payment.amount = quantize_money(payment_amount - remaining_to_apply)
                payment.save(update_fields=['amount'])
                remaining_to_apply = Decimal('0.00')

        applied_amount = quantize_money(to_apply - remaining_to_apply)
        
        if applied_amount > 0:
            # Check if invoice is now paid
            invoice.refresh_from_db()
            if invoice.balance <= 0:
                invoice.mark_paid()

            logger.info(f"Applied {applied_amount} in credits to invoice {invoice.pk}")

        return applied_amount
