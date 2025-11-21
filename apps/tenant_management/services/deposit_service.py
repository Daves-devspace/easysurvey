from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from apps.tenant_management.models import Deposit, LedgerEntry
from apps.tenant_management.services import BaseService
from apps.tenant_management.helpers.money_helpers import quantize_money as q
import logging

logger = logging.getLogger(__name__)

class DepositService(BaseService):
    """Service for handling deposit-related operations."""
    
    @classmethod
    def apply_deposit_to_invoice(cls, deposit, invoice, amount=None):
        """
        Apply part or all of deposit.amount_held to the invoice.
        
        Args:
            deposit: The deposit to apply
            invoice: The invoice to apply to
            amount: Amount to apply (optional, defaults to available amount)
            
        Returns:
            The created LedgerEntry or None if nothing applied
        """
        deposit.refresh_from_db()
        amount_held = q(deposit.amount_held)
        invoice_balance = q(invoice.balance)
        
        if amount is None:
            apply_amount = min(amount_held, invoice_balance)
        else:
            apply_amount = min(q(amount), amount_held, invoice_balance)
        
        apply_amount = q(apply_amount)
        if apply_amount <= Decimal('0.00'):
            return None
        
        # Create ledger entry (credit reduces liability)
        le = LedgerEntry.objects.create(
            lease=deposit.lease,
            tenant=deposit.tenant,
            invoice=invoice,
            deposit=deposit,
            debit=Decimal('0.00'),
            credit=apply_amount,
            entry_type=LedgerEntry.DEPOSIT,
            description=f"Deposit applied to Invoice #{invoice.id} (Deposit #{deposit.pk})"
        )
        
        # Reduce deposit.amount_held
        deposit.amount_held = q(deposit.amount_held - apply_amount)
        deposit.save(update_fields=['amount_held'])
        
        # Create negative invoice line to represent deposit usage
        from apps.tenant_management.models import InvoiceLine
        InvoiceLine.objects.create(
            invoice=invoice,
            lease=deposit.lease,
            meter_reading=None,
            description=f"Deposit applied (Deposit #{deposit.pk})",
            amount=q(-apply_amount),
        )
        
        return le
    
    @classmethod
    def refund_deposit(cls, deposit, amount=None):
        """
        Refund part or all of deposit.amount_held.
        
        Args:
            deposit: The deposit to refund
            amount: Amount to refund (optional, defaults to available amount)
            
        Returns:
            The updated deposit or None if nothing refunded
        """
        deposit.refresh_from_db()
        amount_held = q(deposit.amount_held)
        
        if amount is None:
            refund_amount = amount_held
        else:
            refund_amount = min(q(amount), amount_held)
        
        refund_amount = q(refund_amount)
        if refund_amount <= Decimal('0.00'):
            return None
        
        deposit.refunded_amount = q((deposit.refunded_amount or Decimal('0.00')) + refund_amount)
        deposit.amount_held = q(deposit.amount_held - refund_amount)
        deposit.refunded_at = timezone.now()
        deposit.save(update_fields=['refunded_amount', 'amount_held', 'refunded_at'])
        
        LedgerEntry.objects.create(
            lease=deposit.lease,
            tenant=deposit.tenant,
            deposit=deposit,
            debit=Decimal('0.00'),
            credit=refund_amount,
            entry_type=LedgerEntry.DEPOSIT,
            description=f"Deposit refunded (Deposit #{deposit.pk})"
        )
        
        return deposit