# apps/accounts/cashbook.py
from decimal import Decimal
from apps.accounts.models import CashbookEntry
from django.utils import timezone
from datetime import datetime, time, timedelta  
from django.db import transaction, IntegrityError  
import logging
logger = logging.getLogger(__name__)

def record_cash_in(payment, user=None):
    """Record client payment as cash in (updates running balance)."""
    return CashbookEntry.record_in(
        amount=payment.amount,
        description=f"Client payment from {payment.client_service.client.first_name}",
        related_object=payment,
        created_by=user,
    )


def record_cash_out_institution(amount, user=None, description=None):
    """
    Record cash out for institution payout (generic, not tied to a specific service).
    Guards against insufficient funds.
    """
    amount = Decimal(amount)
    if amount <= 0:
        raise ValueError("Payout amount must be positive")

    current_balance = CashbookEntry.current_balance()
    if current_balance < amount:
        raise ValueError(
            f"Insufficient funds for institution payout. "
            f"Current balance: {current_balance}, requested: {amount}"
        )

    return CashbookEntry.record_out(
        amount=amount,
        description=description or "Institution payout (cash out)",
        created_by=user,
    )




def record_cash_out_expense(description: str, amount, user=None) -> CashbookEntry:
    """
    Record cash out for operational/office expense.
    Enforces: amount > 0 and sufficient funds.
    """
    amount = Decimal(amount)
    if amount <= 0:
        raise ValueError("Expense amount must be positive")

    current_balance = CashbookEntry.current_balance()
    if current_balance < amount:
        raise ValueError(
            f"Insufficient funds for expense '{description}'. "
            f"Current balance: {current_balance}, requested: {amount}"
        )

    return CashbookEntry.record_out(
        amount=amount,
        description=description,
        created_by=user,
    )



