# apps/accounts/services/opening_balance.py
from __future__ import annotations

from decimal import Decimal
from typing import Optional, Dict, Any, Tuple
from datetime import date as _date
from django.apps import apps
from django.db import transaction, IntegrityError
from django.db.models import Sum
from django.utils import timezone

from apps.accounts.models import CashbookEntry
import logging
logger = logging.getLogger(__name__)

# ----- Audit helper (dynamic lookup for AuditLog model) -----
def _get_audit_model():
    """
    Try to resolve the AuditLog model from likely app labels.
    If not found, return None and rely on logging.
    """
    candidates = [
        ("audit", "AuditLog"),
        ("audits", "AuditLog"),
        ("core", "AuditLog"),
        ("accounts", "AuditLog"),
        ("main", "AuditLog"),
        # add more if your project uses a different app label
    ]
    for app_label, model_name in candidates:
        try:
            model = apps.get_model(app_label, model_name)
            if model:
                return model
        except Exception:
            continue
    # try global lookup by model name (slower) — iterate all models
    try:
        for model in apps.get_models():
            if model.__name__ == "AuditLog":
                return model
    except Exception:
        pass
    return None

def log_audit(user, action: str, model_name: str, object_id: Optional[int], description: str, ip_address: Optional[str] = None, user_agent: Optional[str] = None):
    AuditModel = _get_audit_model()
    if AuditModel:
        try:
            AuditModel.objects.create(
                user=user,
                action=action if action else "other",
                model_name=model_name,
                object_id=object_id or 0,
                description=description,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        except Exception as exc:
            logger.exception("Failed to create AuditLog entry: %s", exc)
    else:
        # fallback: log
        logger.info("AUDIT (fallback): user=%s action=%s model=%s id=%s desc=%s",
                    getattr(user, "pk", None), action, model_name, object_id, description)



def get_flagged_opening(entry_date: _date) -> Optional[CashbookEntry]:
    """
    Return the flagged opening CashbookEntry for `entry_date` or None.
    (flagged = is_opening_balance=True)
    """
    return CashbookEntry.objects.filter(entry_date=entry_date, is_opening_balance=True).first()


def get_contributions_total(entry_date: _date) -> Decimal:
    """
    Sum of IN entries on entry_date that are NOT the flagged opening entry.
    These are actual cash-in contributions made for the opening balance.
    """
    qs = CashbookEntry.objects.filter(entry_date=entry_date, entry_type="IN", is_opening_balance=False)
    agg = qs.aggregate(total=Sum("amount"))
    total = agg.get("total") or Decimal("0.00")
    return Decimal(total)


def get_opening_summary(date):
    """
    Returns the opening summary for the given date:
      - flagged: the delta-adjusted opening balance
      - delta: difference from previous day's closing
    """
    flagged_entry = CashbookEntry.objects.filter(
        entry_date=date, is_opening_balance=True
    ).first()

    flagged_balance = Decimal(flagged_entry.balance_after if flagged_entry else 0)
    expected_carried = compute_latest_carried_balance(date)
    delta = Decimal(expected_carried) - flagged_balance

    return {
        "flagged": flagged_balance,
        "delta": delta,
    }




def compute_latest_carried_balance(entry_date: _date) -> Decimal:
    """
    Find the latest CashbookEntry with entry_date < `entry_date` and return its balance_after.
    This allows carry-forward even across multi-day gaps (e.g. Saturday -> Monday).
    """
    prev_entry = (
        CashbookEntry.objects
        .filter(entry_date__lt=entry_date)
        .order_by("-entry_date", "-created_at")
        .first()
    )
    return Decimal(prev_entry.balance_after) if prev_entry else Decimal("0.00")


# ----------------- persist_flagged_opening (non-mutating, logs creation) -----------------
def persist_flagged_opening(entry_date: _date = None, user=None) -> Tuple[CashbookEntry, bool, Decimal]:
    """
    Ensure a flagged opening entry exists for entry_date.

    Returns (flagged_entry, created_bool, carried_balance).
    If flagged existed but had different carried balance, we DO NOT mutate it here (audit rule).
    """
    if entry_date is None:
        entry_date = timezone.now().date()

    with transaction.atomic():
        prev_entry = (
            CashbookEntry.objects
            .filter(entry_date__lt=entry_date)
            .order_by("-entry_date", "-created_at")
            .first()
        )
        carried_balance = prev_entry.balance_after if prev_entry else Decimal("0.00")

        flagged, created = CashbookEntry.objects.get_or_create(
            entry_date=entry_date,
            is_opening_balance=True,
            defaults={
                "entry_type": "IN",
                "amount": Decimal("0.00"),
                "balance_after": carried_balance,
                "description": f"Opening balance (flagged) for {entry_date}",
                "created_by": user,
                "created_at": timezone.make_aware(
                    timezone.datetime.combine(entry_date, timezone.datetime.min.time())
                ),
            }
        )

        # audit-log creation
        if created:
            desc = f"Flagged opening snapshot created for {entry_date} with carried_balance={carried_balance}"
            log_audit(user=user, action="other", model_name="CashbookEntry", object_id=getattr(flagged, "pk", None), description=desc)

        return flagged, created, Decimal(carried_balance)


# ----------------- replace_flagged_snapshot (creates adjustment entry AND logs) -----------------
def replace_flagged_snapshot(entry_date, expected_carried: Decimal, user=None) -> Tuple[CashbookEntry, Decimal]:
    """
    Update the flagged opening balance directly to match expected_carried.
    Returns (flagged_entry, delta).

    - Uses persist_flagged_opening(...) which returns (flagged, created, carried_balance).
    - Uses CashbookEntry.force_update_flagged_balance(...) to bypass audit save() checks
      but still records an AuditLog entry via log_audit.
    """
    with transaction.atomic():
        flagged = CashbookEntry.objects.select_for_update().filter(
            entry_date=entry_date, is_opening_balance=True
        ).first()

        # If no flagged opening exists, create it (persist_flagged_opening returns 3 values)
        if not flagged:
            flagged, created, carried_balance = persist_flagged_opening(entry_date, user=user)
            logger.info("persist_flagged_opening created=%s carried_balance=%s for %s by %s",
                        created, carried_balance, entry_date, getattr(user, "pk", None))
            log_audit(
                user=user,
                action="create",
                model_name="CashbookEntry",
                object_id=flagged.pk,
                description=f"Created new flagged opening snapshot for {entry_date} with balance={carried_balance}"
            )

        flagged_balance = Decimal(flagged.balance_after)
        expected = Decimal(expected_carried)
        delta = expected - flagged_balance

        if delta == Decimal("0.00"):
            # Already in sync → log info
            logger.info("replace_flagged_snapshot: no-op for %s (flagged=%s expected=%s)",
                        entry_date, flagged_balance, expected)
            log_audit(
                user=user,
                action="info",
                model_name="CashbookEntry",
                object_id=flagged.pk,
                description=f"Flagged opening already in sync for {entry_date}. Balance={flagged_balance}"
            )
        else:
            # Update flagged balance using the force-update helper (bypasses save() audit restriction)
            logger.info("replace_flagged_snapshot: updating flagged for %s from %s -> %s (delta=%s) by user=%s",
                        entry_date, flagged_balance, expected, delta, getattr(user, "pk", None))
            flagged.force_update_flagged_balance(expected, user=user)
            log_audit(
                user=user,
                action="update",
                model_name="CashbookEntry",
                object_id=flagged.pk,
                description=(
                    f"Flagged opening balance updated for {entry_date}: "
                    f"old_balance={flagged_balance}, new_balance={expected}, delta={delta}"
                )
            )

        return flagged, delta




def add_opening_contribution(entry_date: _date, amount: Decimal, description: Optional[str] = None, user=None) -> CashbookEntry:
    """
    Add an actual cash-in contribution for the opening balance date.
    This creates a normal IN CashbookEntry (so it's audited and affects running balance).
    Returns the created CashbookEntry.

    Rules:
     - amount must be > 0
     - uses current running balance to compute balance_after (so entries remain in strict chronological order)
    """
    amount = Decimal(amount)
    if amount <= 0:
        raise ValueError("Contribution amount must be positive")

    with transaction.atomic():
        prev_balance = CashbookEntry.current_balance()
        new_balance = prev_balance + amount

        entry = CashbookEntry.objects.create(
            entry_type="IN",
            amount=amount,
            description=description or f"Opening contribution for {entry_date}",
            created_by=user,
            created_at=timezone.now(),
            balance_after=new_balance,
            entry_date=entry_date,
            is_opening_balance=False,
        )
        return entry
