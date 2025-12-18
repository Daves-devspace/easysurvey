from abc import ABC, abstractmethod
from decimal import Decimal
from django.db import transaction
from apps.tenant_management.models import Tenant, Invoice, Payment, InvoiceLine
from apps.tenant_management.helpers.money_helpers import quantize_money as q

import logging

logger = logging.getLogger(__name__)

class PaymentStrategy(ABC):
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
        amount = q(amount)
        
        # 1. Calculate Allocations (Splits Rent vs Deposit correctly)
        allocations = self._calculate_allocations(amount)
        
        # 2. OPTIMIZATION: Single Target Payment
        # Only use this path if there is EXACTLY ONE allocation type.
        # If it split into [Deposit, Rent], this condition fails (len=2), forcing the STANDARD CASE below.
        if len(allocations) == 1 and allocations[0]['amount'] == amount:
            alloc = allocations[0]
            Payment.objects.create(
                tenant=self.tenant,
                invoice=alloc['invoice'],
                amount=amount,
                method=self.method,
                reference=self.reference or "Payment received",
                payment_type=alloc['type']
            )
            
            if alloc['type'] == 'DEPOSIT':
                self._finalize_deposit(alloc['invoice'], amount)
                
            self.applied_to_invoices += amount
            if alloc['type'] == 'DEPOSIT': self.applied_to_deposit += amount
            
            alloc['invoice'].refresh_from_db()
            if alloc['invoice'].balance <= 0: alloc['invoice'].mark_paid()
            
            return self.get_result()

        # 3. STANDARD CASE: Split Payment
        # Create Master Record (Mix of types)
        master_payment = Payment.objects.create(
            tenant=self.tenant,
            invoice=None, 
            amount=amount,
            method=self.method,
            reference=self.reference or "Payment received",
            payment_type='MIXED'
        )
        
        for alloc in allocations:
            Payment.objects.create(
                tenant=self.tenant,
                invoice=alloc['invoice'],
                amount=alloc['amount'],
                method=self.method,
                reference=f"Allocation from payment {master_payment.pk}",
                payment_type=alloc['type']
            )
            
            if alloc['type'] == 'DEPOSIT':
                self._finalize_deposit(alloc['invoice'], alloc['amount'])
            
            self.applied_to_invoices += alloc['amount']
            if alloc['type'] == 'DEPOSIT': self.applied_to_deposit += alloc['amount']
            
            alloc['invoice'].refresh_from_db()
            if alloc['invoice'].balance <= 0: alloc['invoice'].mark_paid()

        # Handle Overpayment (Credit)
        remaining = amount - self.applied_to_invoices
        if remaining > 0:
            Payment.objects.create(
                tenant=self.tenant,
                amount=remaining,
                method=self.method,
                reference=f"Overpayment credit from Payment {master_payment.pk}",
                payment_type='CREDIT'
            )
            self.stored_as_credit = remaining

        return self.get_result()

    def _calculate_allocations(self, amount):
        """
        Determines how to split the payment amount across invoices.
        Crucially splits a single invoice payment into DEPOSIT and RENT portions if needed.
        """
        allocations = []
        remaining = amount
        
        # FIFO: Pay Oldest Invoice First
        unpaid_invoices = Invoice.objects.filter(tenant=self.tenant, is_paid=False).order_by('billing_period_start', 'id')
        
        for invoice in unpaid_invoices:
            if remaining <= 0: break
            
            invoice_balance = q(invoice.balance)
            if invoice_balance <= 0: continue
            
            # Total we can apply to this invoice
            allocate_to_invoice = min(remaining, invoice_balance)
            current_portion = allocate_to_invoice
            
            # FIX: Check if this invoice has an UNPAID DEPOSIT
            deposit_line = invoice.lines.filter(line_type=InvoiceLine.LINE_DEPOSIT).first()
            
            if deposit_line and deposit_line.deposit:
                dep_obj = deposit_line.deposit
                # Calculate how much is still owed on the deposit
                dep_owed = q(dep_obj.amount - dep_obj.amount_held)
                
                if dep_owed > 0:
                    # Allocate to Deposit first (capped by what's owed)
                    # If Current Portion (8000) > Owed (5000), to_deposit = 5000
                    to_deposit = min(current_portion, dep_owed)
                    
                    allocations.append({
                        'invoice': invoice,
                        'amount': to_deposit,
                        'type': 'DEPOSIT'
                    })
                    
                    # Reduce the current portion available for Rent
                    current_portion -= to_deposit
            
            # Apply remainder to Rent/Water (General Balance)
            if current_portion > 0:
                allocations.append({
                    'invoice': invoice,
                    'amount': current_portion,
                    'type': 'RENT'
                })
            
            remaining -= allocate_to_invoice
            
        return allocations

    def _finalize_deposit(self, invoice, amount):
        """Updates the Deposit model to reflect the cash held."""
        from apps.tenant_management.models import LedgerEntry
        from django.utils import timezone
        
        deposit_line = invoice.lines.filter(line_type=InvoiceLine.LINE_DEPOSIT).first()
        if deposit_line and deposit_line.deposit:
            deposit = deposit_line.deposit
            # Ensure we don't hold more than the required amount
            new_held = q(deposit.amount_held + amount)
            deposit.amount_held = min(new_held, deposit.amount)
            
            if deposit.amount_held >= deposit.amount: 
                deposit.paid_at = timezone.now()
            
            deposit.save(update_fields=['amount_held', 'paid_at'])
            
            LedgerEntry.objects.create(
                lease=deposit.lease, tenant=self.tenant, invoice=invoice,
                deposit=deposit, debit=Decimal('0.00'), credit=amount,
                entry_type=LedgerEntry.DEPOSIT,
                description=f"Deposit payment applied"
            )

class CreditApplicationStrategy(PaymentStrategy):
    """Strategy for applying existing tenant credits."""
    def execute(self, amount=None):
        from apps.tenant_management.models import Payment
        
        # Get unallocated credits (FIFO)
        unallocated = Payment.objects.filter(tenant=self.tenant, invoice__isnull=True).exclude(payment_type='MIXED').order_by('payment_date')
        
        for credit_pymt in unallocated:
            if credit_pymt.amount <= 0: continue
            
            # Use shared allocation logic to ensure splits happen here too
            allocations = self._calculate_allocations(credit_pymt.amount)
            
            for alloc in allocations:
                if alloc['amount'] == credit_pymt.amount:
                    credit_pymt.invoice = alloc['invoice']
                    credit_pymt.reference = f"Credit Applied to #{alloc['invoice'].id}"
                    credit_pymt.payment_type = alloc['type'] # Update type to RENT/DEPOSIT
                    credit_pymt.save()
                else:
                    # Partial use: Create new record
                    Payment.objects.create(
                        tenant=self.tenant, invoice=alloc['invoice'], amount=alloc['amount'],
                        method="Credit", reference=f"Credit from #{credit_pymt.pk}", 
                        payment_type=alloc['type'], # RENT or DEPOSIT
                        payment_date=credit_pymt.payment_date
                    )
                    credit_pymt.amount -= alloc['amount']
                    credit_pymt.save()

                if alloc['type'] == 'DEPOSIT':
                    self._finalize_deposit(alloc['invoice'], alloc['amount'])
                
                alloc['invoice'].refresh_from_db()
                if alloc['invoice'].balance <= 0: alloc['invoice'].mark_paid()
                
                self.applied_to_invoices += alloc['amount']
        
        return self.get_result()