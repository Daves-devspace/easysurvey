# =================================================================
# apps/tenant_management/utils/money_helpers.py
# =================================================================

from decimal import Decimal, ROUND_HALF_UP

CENTS = Decimal('0.01')

def quantize_money(value):
    """Convert value to properly rounded Decimal for money operations."""
    if value is None:
        return Decimal('0.00')
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)

def sum_money_values(values):
    """Sum a list of monetary values safely."""
    return sum(quantize_money(v) for v in values if v is not None)

def is_zero_balance(amount):
    """Check if amount is effectively zero (handles rounding)."""
    return abs(quantize_money(amount)) < Decimal('0.01')

def format_currency(amount, currency='KES'):
    """Format amount as currency string."""
    formatted_amount = quantize_money(amount)
    return f"{currency} {formatted_amount:,.2f}"