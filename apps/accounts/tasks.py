from celery import shared_task
from django.utils import timezone
import logging

from apps.accounts.services.opening_balance import persist_flagged_opening, log_audit

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def create_daily_opening_balance(self, user_id=None):
    """
    Celery task that runs after midnight to auto-create the flagged opening
    balance for 'today' if it doesn't already exist.
    Creates an AuditLog entry for traceability.
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()

    # resolve user (if provided), else None
    user = None
    if user_id:
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            logger.warning("User %s not found; proceeding with None", user_id)

    try:
        today = timezone.localdate()
        flagged, created, carried = persist_flagged_opening(today, user=user)

        if created:
            logger.info(f"[OpeningBalanceTask] Created flagged opening for {today} with carried={carried}")
            log_audit(
                user=user,
                action="create",
                model_name="CashbookEntry",
                object_id=flagged.pk,
                description=f"System auto-created flagged opening balance for {today} with carried_balance={carried}"
            )
        else:
            logger.info(f"[OpeningBalanceTask] Flagged opening already exists for {today} (balance={flagged.balance_after})")
            log_audit(
                user=user,
                action="info",
                model_name="CashbookEntry",
                object_id=flagged.pk,
                description=f"System verified flagged opening balance already exists for {today} with balance={flagged.balance_after}"
            )

    except Exception as exc:
        logger.exception("Error while creating daily opening balance: %s", exc)
        raise self.retry(exc=exc)
