# =================================================================
# apps/tenant_management/utils/date_helpers.py
# =================================================================

import calendar
from datetime import date, timedelta

def normalize_billing_day_for_month(year: int, month: int, billing_day: int) -> int:
    """Ensure billing_day doesn't exceed the last day of the month."""
    last_day = calendar.monthrange(year, month)[1]
    return min(billing_day, last_day)

def get_billing_period_for_date(target_date: date, billing_day: int):
    """
    Get billing period (start_date, end_date) for a given date and billing day.
    
    Logic:
    - If date.day < billing_day → belongs to previous period
    - If date.day >= billing_day → belongs to current period
    """
    current_month_billing_day = normalize_billing_day_for_month(
        target_date.year, target_date.month, billing_day
    )

    if target_date.day < current_month_billing_day:
        # Previous period
        if target_date.month == 1:
            prev_month = 12
            year = target_date.year - 1
        else:
            prev_month = target_date.month - 1
            year = target_date.year

        start_day = normalize_billing_day_for_month(year, prev_month, billing_day)
        start_date = date(year, prev_month, start_day)
        end_date = date(target_date.year, target_date.month, current_month_billing_day)
    else:
        # Current period
        start_date = date(target_date.year, target_date.month, current_month_billing_day)
        if target_date.month == 12:
            next_month = 1
            year = target_date.year + 1
        else:
            next_month = target_date.month + 1
            year = target_date.year
        
        end_day = normalize_billing_day_for_month(year, next_month, billing_day)
        end_date = date(year, next_month, end_day)

    return start_date, end_date

def get_billing_period_for_month(billing_month: date, billing_day: int):
    """
    Get billing period for a specific month.
    billing_month should be first day of the target month.
    """
    year = billing_month.year
    month = billing_month.month

    start_day = normalize_billing_day_for_month(year, month, billing_day)
    start_date = date(year, month, start_day)

    # Next month
    if month == 12:
        next_month = 1
        next_year = year + 1
    else:
        next_month = month + 1
        next_year = year

    end_day = normalize_billing_day_for_month(next_year, next_month, billing_day)
    end_date = date(next_year, next_month, end_day)

    return start_date, end_date

def is_first_invoice_for_lease(lease, billing_period_start):
    """Check if this would be the first invoice for a specific lease."""
    from apps.tenant_management.models import InvoiceLine
    return not InvoiceLine.objects.filter(
        lease=lease,
        line_type=InvoiceLine.LINE_DEPOSIT
    ).exists()

