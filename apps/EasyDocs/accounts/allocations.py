# apps/EasyDocs/accounts/allocations.py
"""
Pure allocation logic (no DB locking here).
Called by the signal code which is responsible for acquiring row locks
(select_for_update) inside a transaction.atomic().

Function:
    allocate_payment_shares(payment, service_processes=None, sub_services=None)
Returns:
    List of dicts:
      {
        'target': <model instance or None>,
        'target_type': 'service_step' | 'subservice' | 'ground',
        'gross': Decimal(...),
        'institution': Decimal(...),
        'company': Decimal(...)
      }
Notes:
  - If service_processes or sub_services are provided, they are *used as-is*
    (assumed locked by the caller). If not provided, this function will
    fall back to non-locked iteration (useful for tests or non-concurrent code).
"""
from decimal import Decimal
from apps.EasyDocs.models import ServiceCategory  # adjust import path if needed
from django.db import transaction
import logging
logger = logging.getLogger(__name__)
def allocate_payment_shares(payment, service_processes=None, sub_services=None):
    """
    Distributes a payment in correct priority:
      1️⃣ Main ClientService processes (oldest first)
      2️⃣ Then SubServices (oldest added_on first)
    Each subservice uses snapshot prices to freeze revenue proportions.
    """
    allocations = []
    remaining = Decimal(payment.amount).quantize(Decimal('0.01'))
    q = lambda v: Decimal(v).quantize(Decimal('0.01'))

    client_service = payment.client_service
    service = client_service.service

    logger.info(f"Allocating payment #{payment.id}: {remaining} KES for Service {service.name}")

    # --- Step 1: Settle ClientService steps first
    s_processes = service_processes or []
    for step in s_processes:
        if remaining <= 0:
            break

        step_cost = getattr(step, 'cost', None)
        if step_cost is None:
            step_cost = (
                step.overridden_cost
                if step.overridden_cost is not None
                else getattr(step.process, 'cost', Decimal('0.00'))
            )

        pending = Decimal(step_cost or Decimal('0.00')) - Decimal(step.paid_amount or Decimal('0.00'))
        if pending <= 0:
            continue

        pay_amount = min(remaining, pending)
        allocations.append({
            'target': step,
            'target_type': 'service_step',
            'gross': q(pay_amount),
            'institution': Decimal('0.00'),
            'company': q(pay_amount),
        })
        remaining -= pay_amount
        logger.debug(f"  Step #{step.id} settled {pay_amount} KES; remaining={remaining}")

    # --- Step 2: Subservices (oldest first)
    subs = sorted(sub_services or [], key=lambda s: s.added_on)
    for sub in subs:
        if remaining <= 0:
            break

        effective_price = (
            sub.overridden_price_snapshot or
            sub.overridden_price or
            sub.sub_service.price or Decimal('0.00')
        )
        inst_cost = (
            sub.institution_cost_snapshot or
            sub.sub_service.price or Decimal('0.00')
        )

        pending = (effective_price or Decimal('0.00')) - (sub.paid_amount or Decimal('0.00'))
        if pending <= 0:
            continue

        pay_amount = min(remaining, pending)

        if effective_price > 0:
            inst_share = q(pay_amount * inst_cost / effective_price)
        else:
            inst_share = Decimal('0.00')
        company_share = q(pay_amount - inst_share)

        allocations.append({
            'target': sub,
            'target_type': 'subservice',
            'gross': q(pay_amount),
            'institution': inst_share,
            'company': company_share,
        })

        remaining -= pay_amount
        logger.debug(f"  Subservice #{sub.id}: paid={pay_amount}, inst={inst_share}, comp={company_share}, remaining={remaining}")

    if remaining > 0:
        logger.info(f"⚠️ {remaining} KES unallocated (overpayment or no pending balances).")

    return allocations
