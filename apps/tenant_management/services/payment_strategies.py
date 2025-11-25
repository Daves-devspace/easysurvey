from abc import ABC, abstractmethod
from decimal import Decimal
from apps.tenant_management.models import Tenant, Invoice, Payment
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
        
        # 1. Calculate Allocations
        allocations = self._calculate_allocations(amount)
        
        # 2. OPTIMIZATION: Single Target Payment
        # If the payment pays off exactly ONE item, create just ONE record.
        if len(allocations) == 1 and allocations[0]['amount'] == amount:
            alloc = allocations[0]
            Payment.objects.create(
                tenant=self.tenant,
                invoice=alloc['invoice'],
                amount=amount,
                method=self.method,
                reference=self.reference or "Payment received",
                payment_type=alloc['type'] # RENT or DEPOSIT
            )
            
            if alloc['type'] == 'DEPOSIT':
                self._finalize_deposit(alloc['invoice'], amount)
                
            self.applied_to_invoices += amount
            if alloc['type'] == 'DEPOSIT': self.applied_to_deposit += amount
            
            alloc['invoice'].refresh_from_db()
            if alloc['invoice'].balance <= 0: alloc['invoice'].mark_paid()
            
            return self.get_result()

        # 3. STANDARD CASE: Split Payment (Master + Children)
        # Master Record: Holds the total cash received. NO INVOICE ID.
        master_payment = Payment.objects.create(
            tenant=self.tenant,
            invoice=None, # CRITICAL: Must be None to prevent double counting in utils.py
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
        allocations = []
        remaining = amount
        
        # FIFO: Oldest First
        unpaid_invoices = Invoice.objects.filter(tenant=self.tenant, is_paid=False).order_by('billing_period_start', 'id')
        
        for invoice in unpaid_invoices:
            if remaining <= 0: break
            
            invoice_balance = q(invoice.balance)
            if invoice_balance <= 0: continue
            
            allocate = min(remaining, invoice_balance)
            
            from apps.tenant_management.models import InvoiceLine
            has_deposit = invoice.lines.filter(line_type=InvoiceLine.LINE_DEPOSIT).exists()
            p_type = 'DEPOSIT' if has_deposit else 'RENT'
            
            allocations.append({
                'invoice': invoice,
                'amount': allocate,
                'type': p_type
            })
            remaining -= allocate
            
        return allocations

    def _finalize_deposit(self, invoice, amount):
        from apps.tenant_management.models import InvoiceLine, LedgerEntry
        from django.utils import timezone
        deposit_line = invoice.lines.filter(line_type=InvoiceLine.LINE_DEPOSIT).first()
        if deposit_line and deposit_line.deposit:
            deposit = deposit_line.deposit
            deposit.amount_held = q(deposit.amount_held + amount)
            if deposit.amount_held >= deposit.amount: deposit.paid_at = timezone.now()
            deposit.save(update_fields=['amount_held', 'paid_at'])
            
            LedgerEntry.objects.create(
                lease=deposit.lease, tenant=self.tenant, invoice=invoice,
                deposit=deposit, debit=Decimal('0.00'), credit=amount,
                entry_type=LedgerEntry.DEPOSIT,
                description=f"Deposit payment applied"
            )

class CreditApplicationStrategy(PaymentStrategy):
    def execute(self, amount=None):
        # FIFO Logic for Credits
        from apps.tenant_management.models import Payment
        unallocated = Payment.objects.filter(tenant=self.tenant, invoice__isnull=True).exclude(payment_type='MIXED')
        
        for credit_pymt in unallocated:
            if credit_pymt.amount <= 0: continue
            
            allocations = self._calculate_allocations(credit_pymt.amount)
            
            for alloc in allocations:
                if alloc['amount'] == credit_pymt.amount:
                    credit_pymt.invoice = alloc['invoice']
                    credit_pymt.reference = f"Credit Applied to #{alloc['invoice'].id}"
                    credit_pymt.save()
                else:
                    Payment.objects.create(
                        tenant=self.tenant, invoice=alloc['invoice'], amount=alloc['amount'],
                        method="Credit", reference=f"Credit from #{credit_pymt.pk}", payment_type='CREDIT',
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