from abc import ABC, abstractmethod
from decimal import Decimal
from django.db import transaction
from apps.tenant_management.models import Tenant, Invoice, Payment
from apps.tenant_management.helpers.money_helpers import quantize_money as q
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
        pass
    
    def get_result(self):
        balance = getattr(self.tenant, 'balance', None)
        return {
            "applied_to_deposit": str(self.applied_to_deposit),
            "applied_to_invoices": str(self.applied_to_invoices),
            "stored_as_credit": str(self.stored_as_credit),
            "unallocated": "0.00",
            "tenant_balance": str(balance.balance if balance else '0.00')
        }

class PaymentStrategy(PaymentStrategy):
    """Strategy for processing new payments."""
    
    def execute(self, amount):
        amount = q(amount)
        logger.info(f"Processing new payment of {amount} for tenant {self.tenant.full_name}")
        
        # 1. Create Master Payment (Initially Unallocated)
        master_payment = Payment.objects.create(
            tenant=self.tenant,
            invoice=None,
            amount=amount,
            method=self.method,
            reference=self.reference or "Payment received",
            payment_type='MIXED' # Will be updated later
        )
        
        # 2. Apply to Invoices
        remaining = self._apply_to_invoices(amount, master_payment)
        
        # 3. Store Remaining as Credit
        if remaining > 0:
            # If we have remaining money, the Master Payment becomes a "Split" parent.
            # We need to create a specific Credit record for the remainder.
            self.stored_as_credit = remaining
            self._store_as_credit(remaining, master_payment)
            
            # Update master payment amount to reflect it is a container? 
            # No, typically in this model, we reduce the master amount OR keep master as "Total Received" and have children sum up.
            # To avoid double counting in simple SUM queries:
            # We will reduce the Master Payment amount by the amount allocated to invoices/credit 
            # OR (Better for your view) Ensure the View filters out "Parent" payments if "Children" exist.
            
            # SIMPLER APPROACH FOR YOUR TABLE: 
            # If splits happened, the Master Payment should conceptually "disappear" into its children 
            # OR be marked as a "Parent" that isn't summed.
            pass 
        
        return self.get_result()
    
    def _apply_to_invoices(self, amount, master_payment):
        remaining = amount
        unpaid_invoices = Invoice.objects.filter(tenant=self.tenant, is_paid=False).order_by('billing_period_start', 'id')
        
        created_allocations = [] # Track created objects

        for invoice in unpaid_invoices:
            if remaining <= 0: break
            invoice_balance = q(invoice.balance)
            if invoice_balance <= 0: continue
            
            allocate = min(remaining, invoice_balance)
            deposit_allocation = self._apply_to_deposit_lines(invoice, allocate, master_payment)
            
            # Create CHILD payment
            allocation_pymt = Payment.objects.create(
                tenant=self.tenant,
                invoice=invoice,
                amount=allocate,
                method=self.method,
                reference=f"Allocation from payment {master_payment.pk}",
                payment_type='DEPOSIT' if deposit_allocation == allocate else 'RENT'
            )
            created_allocations.append(allocation_pymt)
            
            self.applied_to_invoices += allocate
            self.applied_to_deposit += deposit_allocation
            remaining -= allocate
            
            invoice.refresh_from_db()
            if invoice.balance <= 0 and not invoice.is_paid:
                invoice.mark_paid()

        # --- FIX FOR DUPLICATE RECORDS ---
        # If exactly one allocation covered the whole payment (Simple Case),
        # we don't want a Master Record AND a Child Record.
        # We merge them.
        if len(created_allocations) == 1 and remaining == 0:
            single_child = created_allocations[0]
            
            # Update Master to look like the Child
            master_payment.invoice = single_child.invoice
            master_payment.payment_type = single_child.payment_type
            master_payment.save(update_fields=['invoice', 'payment_type'])
            
            # Delete the Child (Prevent Duplication)
            single_child.delete()
            
        elif len(created_allocations) > 0:
            # Complex Case (Split Payment or Partial Payment)
            # We have a Master (Total) and Children (Splits).
            # To prevent your table from summing Master + Children (Double Counting),
            # We must decide a strategy. 
            # Strategy: Master Payment represents the "Transaction".
            # We can mark the Master as "SPLIT" so the View knows to ignore it or handle it differently.
            master_payment.payment_type = 'MIXED'
            master_payment.save(update_fields=['payment_type'])

        return remaining

    def _apply_to_deposit_lines(self, invoice, amount, master_payment):
        # (Logic remains unchanged - assumes DepositService handles Ledger)
        from apps.tenant_management.models import InvoiceLine, Deposit, LedgerEntry
        from django.utils import timezone
        
        deposit_lines = invoice.lines.filter(line_type=InvoiceLine.LINE_DEPOSIT)
        total_allocated = Decimal('0.00')
        
        for line in deposit_lines:
            if amount <= 0: break
            deposit = line.deposit
            if deposit and not deposit.paid_at:
                needed = q(deposit.amount - deposit.amount_held)
                if needed > 0:
                    allocate = min(amount, needed)
                    deposit.amount_held = q(deposit.amount_held + allocate)
                    if deposit.amount_held >= deposit.amount:
                        deposit.paid_at = timezone.now()
                    deposit.save(update_fields=['amount_held', 'paid_at'])
                    
                    # Ledger Entry creation (omitted for brevity, keep existing logic)
                    total_allocated += allocate
                    amount -= allocate
        return total_allocated

    def _store_as_credit(self, amount, master_payment):
        # If we are storing credit, we create a specific credit record.
        # Check if Master is pristine (no invoice allocations). 
        # If Master is pristine, just convert Master to Credit.
        if master_payment.amount == amount and not master_payment.invoice:
            master_payment.payment_type = 'CREDIT'
            master_payment.reference = f"Credit (Overpayment)"
            master_payment.save()
        else:
            # Master was split. Create new Credit record.
            Payment.objects.create(
                tenant=self.tenant,
                invoice=None,
                amount=amount,
                method=self.method,
                reference=f"Credit from Payment #{master_payment.pk}",
                payment_type='CREDIT'
            )

class CreditApplicationStrategy(PaymentStrategy):
    """Strategy for applying existing tenant credits to invoices."""
    
    def execute(self, amount=None):
        logger.info(f"Applying tenant credits for tenant {self.tenant.full_name}")
        
        unallocated_payments = Payment.objects.filter(
            tenant=self.tenant, invoice__isnull=True
        ).order_by('payment_date')
        
        unpaid_invoices = Invoice.objects.filter(
            tenant=self.tenant, is_paid=False
        ).order_by('billing_period_start', 'id')
        
        for payment in unallocated_payments:
            if payment.amount <= 0: continue
            remaining_payment = payment.amount
            
            for invoice in unpaid_invoices:
                if remaining_payment <= 0: break
                invoice_balance = q(invoice.balance)
                if invoice_balance <= 0: continue
                
                allocate = min(remaining_payment, invoice_balance)
                
                # 1. Update or Split the Credit Payment
                if allocate == remaining_payment:
                    # The WHOLE credit payment is used for this one invoice.
                    # Just link it. No new record needed.
                    payment.invoice = invoice
                    payment.reference = f"Credit applied to Inv #{invoice.id}"
                    payment.payment_type = 'CREDIT'
                    payment.save(update_fields=['invoice', 'reference', 'payment_type'])
                    # payment object is now "consumed" / allocated
                else:
                    # Partial use. We must SPLIT.
                    # Create a new record for the used portion linked to invoice
                    Payment.objects.create(
                        tenant=self.tenant,
                        invoice=invoice,
                        amount=allocate,
                        method=payment.method,
                        reference=f"Credit applied to Inv #{invoice.id}",
                        payment_type='CREDIT',
                        payment_date=payment.payment_date
                    )
                    # Reduce the original credit record
                    payment.amount -= allocate
                    payment.save(update_fields=['amount'])
                
                remaining_payment -= allocate
                self.applied_to_invoices += allocate
                
                invoice.refresh_from_db()
                if invoice.balance <= 0 and not invoice.is_paid:
                    invoice.mark_paid()

        return self.get_result()