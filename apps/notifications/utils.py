from typing import Iterable, Tuple, Union
import hashlib
import logging
from firebase_admin import messaging
from django.core.cache import cache
from django.db.models import QuerySet
from .models import FCMToken, PendingPushNotification
from .firebase_manager import initialize_firebase

logger = logging.getLogger(__name__)


def _push_dedupe_key(user_id: int, title: str, body: str) -> str:
    digest = hashlib.sha256(f"{user_id}\n{title}\n{body}".encode("utf-8")).hexdigest()
    return f"notifications:push-dedupe:{digest}"

def _deactivate_token(token: str) -> None:
    try:
        FCMToken.objects.filter(token=token).update(is_active=False)
        logger.warning("Deactivated invalid FCM token: %s", token)
    except Exception:
        logger.exception("Failed to deactivate token: %s", token)

def send_push_notification(token: str, title: str, body: str) -> bool:
    """
    Send a single message. Initializes Firebase first.
    """
    # 1. Ensure Firebase is ready
    if not initialize_firebase():
        return False

    message = messaging.Message(
        notification=messaging.Notification(title=title, body=body),
        token=token,
    )
    try:
        resp = messaging.send(message)
        logger.info("✅ Push sent to token %s: %s", token, resp)
        return True
    except Exception as exc:
        logger.error("❌ send_push_notification failed for token %s: %s", token, exc)
        err = str(exc)
        if "registration-token-not-registered" in err or "invalid-registration-token" in err:
            _deactivate_token(token)
        return False


def send_push_to_user(
    users: Union[object, Iterable[object]],
    title: str,
    body: str,
    dedupe_window_seconds: int = 15,
) -> Tuple[int, int]:
    """
    Send push to user(s). Returns (success_count, failure_count).
    """
    # 1. Ensure Firebase is ready
    if not initialize_firebase():
        return 0, 0

    if isinstance(users, (list, tuple, QuerySet)):
        user_list = list(users)
    else:
        user_list = [users]

    total_success = 0
    total_failure = 0
    try:
        dedupe_window_seconds = max(int(dedupe_window_seconds or 0), 1)
    except (TypeError, ValueError):
        dedupe_window_seconds = 15

    for user in user_list:
        user_id = getattr(user, "id", None)
        if user_id is None:
            logger.warning("Skipping push send for object without user id: %s", user)
            continue

        dedupe_key = _push_dedupe_key(user_id, title, body)
        if not cache.add(dedupe_key, "1", timeout=dedupe_window_seconds):
            logger.info("Skipping duplicate push for user %s within dedupe window", user_id)
            continue

        try:
            tokens = list(
                FCMToken.objects.filter(user=user, is_active=True).values_list("token", flat=True)
            )
        except Exception:
            logger.exception("Failed to fetch tokens for user %s", user)
            continue

        if not tokens:
            logger.warning("⚠️ No active FCM tokens for %s — saving pending", user)
            try:
                PendingPushNotification.objects.create(user=user, title=title, body=body)
            except Exception:
                logger.exception("Failed to create pending push for user %s", user)
            total_failure += 1
            continue

        messages = [
            messaging.Message(notification=messaging.Notification(title=title, body=body), token=t)
            for t in tokens
        ]

        # Use send_all for efficiency if available
        try:
            if hasattr(messaging, "send_all"):
                batch_resp = messaging.send_all(messages)
                success_count = sum(1 for r in batch_resp.responses if getattr(r, "success", False))
                failure_count = len(batch_resp.responses) - success_count
                total_success += success_count
                total_failure += failure_count

                for i, r in enumerate(batch_resp.responses):
                    if not getattr(r, "success", False):
                        exc_str = str(getattr(r, "exception", r))
                        if "registration-token-not-registered" in exc_str or "invalid-registration-token" in exc_str:
                            _deactivate_token(tokens[i])
            else:
                # Fallback loop
                for tkn in tokens:
                    if send_push_notification(tkn, title, body):
                        total_success += 1
                    else:
                        total_failure += 1

        except Exception as exc:
            logger.exception("FCM batch send failed for user %s: %s", user, exc)
            total_failure += len(tokens)

    return total_success, total_failure