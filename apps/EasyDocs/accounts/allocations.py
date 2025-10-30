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
    Core allocation function.
    1️⃣ Pay service processes (ClientServiceProcess) sequentially by step order.
    2️⃣ Then pay subservices sequentially by added_on.
    Each subservice computes institution/company shares independently.
    """
    allocations = []
    remaining = Decimal(str(payment.amount))
    client_service = payment.client_service
    service = client_service.service
    q = lambda v: Decimal(v).quantize(Decimal('0.01'))

    logger.info(f"💡 Starting allocation logic for Payment #{payment.id}: amount={remaining}")

    # 1️⃣ Settle service processes first
    s_processes = service_processes if service_processes is not None else []
    for csp in s_processes:
        if remaining <= 0:
            break
        pending = (csp.overridden_cost or Decimal('0.00')) - (csp.paid_amount or Decimal('0.00'))
        if pending <= 0:
            continue

        pay_amount = min(remaining, pending)
        allocations.append({
            'target': csp,
            'target_type': 'service_step',
            'gross': q(pay_amount),
            'institution': Decimal('0.00'),
            'company': q(pay_amount),
        })
        remaining -= pay_amount
        logger.info(f"  ⚙️ Settled process #{csp.id} with {pay_amount} KES, remaining={remaining}")

    # 2️⃣ Move to subservices
    subs = sub_services if sub_services is not None else []
    for sub in subs:
        if remaining <= 0:
            break

        pending = (sub.price or Decimal('0.00')) - (sub.paid_amount or Decimal('0.00'))
        if pending <= 0:
            continue

        pay_amount = min(remaining, pending)
        inst_cost = sub.sub_service.price or Decimal('0.00')
        charge_price = sub.overridden_price or sub.sub_service.price or Decimal('0.00')

        if charge_price > 0:
            inst_share = (pay_amount * inst_cost / charge_price).quantize(Decimal('0.01'))
        else:
            inst_share = Decimal('0.00')

        company_share = (pay_amount - inst_share).quantize(Decimal('0.01'))

        allocations.append({
            'target': sub,
            'target_type': 'subservice',
            'gross': q(pay_amount),
            'institution': q(inst_share),
            'company': q(company_share),
        })
        remaining -= pay_amount
        logger.info(
            f"  🧩 Subservice #{sub.id}: Paid {pay_amount}, inst={inst_share}, "
            f"profit={company_share}, remaining={remaining}"
        )

    if remaining > 0:
        logger.info(f"  ⚠️ {remaining} KES unallocated (possibly overpayment or no pending balances)")

    return allocations
