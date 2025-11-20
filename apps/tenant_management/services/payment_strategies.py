from abc import ABC, abstractmethod
from decimal import Decimal
from django.db import transaction
from apps.tenant_management.models import Tenant, Invoice, Payment
from apps.tenant_management.utils.payment_utils import q
from apps.tenant_management.exceptions import PaymentProcessingError
import logging

logger = logging.getLogger(__name__)

class PaymentStrategy(ABC):
    """Abstract base class for payment strategies."""
    
    def __init__(self, tenant, reference=None, method="Mpesa"):
        self.tenant = tenant
        self.reference = reference
        self.method = method
        self.applied_to_invoices = Decimal('0.00')
        self.applied_to_deposit = Decimal('0.00')
        self.stored_as_credit = Decimal('0.00')
    
    @abstractmethod
    def execute(self, amount):
        """Execute the payment strategy with the given amount."""
        pass
    
    def get_result(self):
        """Return the result of the payment processing."""
        return {
            "applied_to_deposit": str(self.applied_to_deposit),
            "applied_to_invoices": str(self.applied_to_invoices),
            "stored_as_credit": str(self.stored_as_credit),
            "unallocated": "0.00",
            "tenant_balance": str(self.tenant.balance.balance if hasattr(self.tenant, 'balance') else '0.00')
        }

class PaymentStrategy(PaymentStrategy):
    """Strategy for processing new payments."""
    
    def execute(self, amount):
        """Process a new payment from external source."""
        amount = q(amount)
        logger.info(f"Processing new payment of {amount} for tenant {self.tenant.full_name}")
        
        # Create the master payment record
        master_payment = Payment.objects.create(
            tenant=self.tenant,
            invoice=None,  # Will be updated if applied to single invoice
            amount=amount,
            method=self.method,
            reference=self.reference or "Payment received",
            payment_type='MIXED'
        )
        
        # Apply payment to invoices
        remaining = self._apply_to_invoices(amount, master_payment)
        
        # Store any remaining as credit
        if remaining > 0:
            self.stored_as_credit = remaining
            self._store_as_credit(remaining, master_payment)
        
        return self.get_result()
    
    def _apply_to_invoices(self, amount, master_payment):
        """Apply payment to unpaid invoices."""
        remaining = amount
        
        # Get unpaid invoices ordered by billing period (oldest first)
        unpaid_invoices = Invoice.objects.filter(
            tenant=self.tenant, 
            is_paid=False
        ).order_by('billing_period_start', 'id')
        
        paid_invoice_ids = []
        
        for invoice in unpaid_invoices:
            if remaining <= 0:
                break
                
            invoice_balance = q(invoice.balance)
            if invoice_balance <= 0:
                continue
                
            allocate = min(remaining, invoice_balance)
            
            # Apply to deposit lines first if present
            deposit_allocation = self._apply_to_deposit_lines(invoice, allocate, master_payment)
            
            # Create payment record for this allocation
            payment_record = Payment.objects.create(
                tenant=self.tenant,
                invoice=invoice,
                amount=allocate,
                method=self.method,
                reference=f"Allocation from payment {master_payment.pk}",
                payment_type='DEPOSIT' if deposit_allocation == allocate else 'RENT'
            )
            
            paid_invoice_ids.append(invoice.pk)
            self.applied_to_invoices += allocate
            self.applied_to_deposit += deposit_allocation
            remaining -= allocate
            
            # Update invoice status
            invoice.refresh_from_db()
            if invoice.balance <= 0 and not invoice.is_paid:
                invoice.mark_paid()
        
        # If only one invoice was paid, update master payment record
        if len(paid_invoice_ids) == 1:
            invoice_obj = Invoice.objects.get(pk=paid_invoice_ids[0])
            master_payment.invoice = invoice_obj
            master_payment.save(update_fields=['invoice'])
        
        return remaining
    
    def _apply_to_deposit_lines(self, invoice, amount, master_payment):
        """Apply payment to deposit lines within an invoice."""
        from apps.tenant_management.models import InvoiceLine, Deposit, LedgerEntry
        from django.utils import timezone
        
        deposit_lines = invoice.lines.filter(line_type=InvoiceLine.LINE_DEPOSIT)
        if not deposit_lines:
            return Decimal('0.00')
        
        remaining = amount
        total_allocated = Decimal('0.00')
        
        for line in deposit_lines:
            if remaining <= 0:
                break
                
            deposit = line.deposit
            if deposit and not deposit.paid_at:
                deposit_needed = q(deposit.amount - deposit.amount_held)
                if deposit_needed > 0:
                    allocate = min(remaining, deposit_needed)
                    
                    # Update deposit
                    deposit.amount_held = q(deposit.amount_held + allocate)
                    if deposit.amount_held >= deposit.amount:
                        deposit.paid_at = timezone.now()
                    deposit.save(update_fields=['amount_held', 'paid_at'])
                    
                    # Create ledger entry
                    LedgerEntry.objects.create(
                        lease=deposit.lease,
                        tenant=self.tenant,
                        invoice=invoice,
                        deposit=deposit,
                        debit=Decimal('0.00'),
                        credit=allocate,
                        entry_type=LedgerEntry.DEPOSIT,
                        description=f"Deposit payment from Payment #{master_payment.pk}"
                    )
                    
                    remaining -= allocate
                    total_allocated += allocate
        
        return total_allocated
    
    def _store_as_credit(self, amount, master_payment):
        """Store remaining amount as tenant credit."""
        from apps.tenant_management.models import Payment
        
        # Create unallocated payment record
        Payment.objects.create(
            tenant=self.tenant,
            invoice=None,
            amount=amount,
            method=self.method,
            reference=f"Overpayment credit from Payment #{master_payment.pk}",
            payment_type='CREDIT'
        )

class CreditApplicationStrategy(PaymentStrategy):
    """Strategy for applying existing tenant credits to invoices."""
    
    def execute(self, amount=None):
        """Apply existing tenant credits to unpaid invoices."""
        logger.info(f"Applying tenant credits for tenant {self.tenant.full_name}")
        
        # Get unallocated payments (tenant credits)
        unallocated_payments = Payment.objects.filter(
            tenant=self.tenant,
            invoice__isnull=True
        ).order_by('payment_date')
        
        total_credit = sum(q(p.amount) for p in unallocated_payments)
        
        if total_credit <= 0:
            logger.info("No unallocated credits available")
            return self.get_result()
        
        # Get unpaid invoices ordered by billing period (newest first for credit application)
        unpaid_invoices = Invoice.objects.filter(
            tenant=self.tenant, 
            is_paid=False
        ).order_by('-billing_period_start', '-id')
        
        # Apply credits to invoices
        for payment in unallocated_payments:
            if payment.amount <= 0:
                continue
                
            remaining_payment = payment.amount
            
            for invoice in unpaid_invoices:
                if remaining_payment <= 0:
                    break
                    
                invoice_balance = q(invoice.balance)
                if invoice_balance <= 0:
                    continue
                    
                allocate = min(remaining_payment, invoice_balance)
                
                # Apply to deposit lines first if present
                deposit_allocation = self._apply_to_deposit_lines(invoice, allocate, payment)
                
                # Update payment record
                if allocate == payment.amount:
                    # Use entire payment
                    payment.invoice = invoice
                    payment.reference = f"Credit applied to Invoice {invoice.pk}"
                    payment.payment_type = 'CREDIT'
                    payment.save(update_fields=['invoice', 'reference', 'payment_type'])
                else:
                    # Split payment
                    Payment.objects.create(
                        tenant=self.tenant,
                        invoice=invoice,
                        amount=allocate,
                        method=payment.method,
                        reference=f"Credit applied to Invoice {invoice.pk}",
                        payment_type='CREDIT',
                        payment_date=payment.payment_date
                    )
                    payment.amount -= allocate
                    payment.save(update_fields=['amount'])
                
                self.applied_to_invoices += allocate
                self.applied_to_deposit += deposit_allocation
                remaining_payment -= allocate
                
                # Update invoice status
                invoice.refresh_from_db()
                if invoice.balance <= 0 and not invoice.is_paid:
                    invoice.mark_paid()
            
            # If there's any remaining payment amount after processing all invoices
            if remaining_payment > 0:
                payment.amount = remaining_payment
                payment.save(update_fields=['amount'])
        
        return self.get_result()
    
    def _apply_to_deposit_lines(self, invoice, amount, payment):
        """Apply credit to deposit lines within an invoice."""
        from apps.tenant_management.models import InvoiceLine, Deposit, LedgerEntry
        from django.utils import timezone
        
        deposit_lines = invoice.lines.filter(line_type=InvoiceLine.LINE_DEPOSIT)
        if not deposit_lines:
            return Decimal('0.00')
        
        remaining = amount
        total_allocated = Decimal('0.00')
        
        for line in deposit_lines:
            if remaining <= 0:
                break
                
            deposit = line.deposit
            if deposit and not deposit.paid_at:
                deposit_needed = q(deposit.amount - deposit.amount_held)
                if deposit_needed > 0:
                    allocate = min(remaining, deposit_needed)
                    
                    # Update deposit
                    deposit.amount_held = q(deposit.amount_held + allocate)
                    if deposit.amount_held >= deposit.amount:
                        deposit.paid_at = timezone.now()
                    deposit.save(update_fields=['amount_held', 'paid_at'])
                    
                    # Create ledger entry
                    LedgerEntry.objects.create(
                        lease=deposit.lease,
                        tenant=self.tenant,
                        invoice=invoice,
                        deposit=deposit,
                        debit=Decimal('0.00'),
                        credit=allocate,
                        entry_type=LedgerEntry.DEPOSIT,
                        description=f"Deposit payment from Credit Payment #{payment.pk}"
                    )
                    
                    remaining -= allocate
                    total_allocated += allocate
        
        return total_allocated