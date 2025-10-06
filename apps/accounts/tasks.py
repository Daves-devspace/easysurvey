# apps/accounts/tasks.py
from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from celery import shared_task
from django.utils import timezone
from django.contrib.auth import get_user_model
import logging

from apps.accounts.services.opening_balance import compute_latest_carried_balance, replace_flagged_snapshot
from apps.accounts.services.opening_balance import _get_audit_model, log_audit  # if you prefer direct access
from apps.accounts.models import CashbookEntry

logger = logging.getLogger(__name__)
User = get_user_model()

@shared_task(bind=True)
def reconcile_flagged_opening_task(self, entry_date_iso: str, user_id: int = None):
    """
    Background reconcile task:
      - compute expected carried balance
      - call replace_flagged_snapshot(...) to create adjustment if needed
      - creates AuditLog entry via service functions (they already log)
    Returns summary dict.
    """
    try:
        entry_date = datetime.strptime(entry_date_iso, "%Y-%m-%d").date()
    except Exception:
        entry_date = timezone.now().date()

    user = None
    if user_id:
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            user = None

    expected = compute_latest_carried_balance(entry_date)
    flagged, delta, adjustment = replace_flagged_snapshot(entry_date, Decimal(expected), user=user)

    result = {
        "entry_date": entry_date_iso,
        "flagged_id": getattr(flagged, "pk", None),
        "delta": float(delta),
        "adjustment_id": getattr(adjustment, "pk", None) if adjustment else None,
    }
    logger.info("Reconcile finished: %s", result)

    # Extra audit entry (task run)
    desc = f"Reconcile task ran for {entry_date_iso}. delta={delta}, adjustment_id={result['adjustment_id']}"
    log_audit(user=user, action="other", model_name="ReconcileTask", object_id=0, description=desc)

    return result
