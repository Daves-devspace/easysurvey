# apps/notifications/utils.py
from typing import Iterable, List, Tuple, Union
import logging

from firebase_admin import messaging
from django.db.models import QuerySet

from .models import FCMToken, PendingPushNotification

logger = logging.getLogger(__name__)


def _deactivate_token(token: str) -> None:
    try:
        FCMToken.objects.filter(token=token).update(is_active=False)
        logger.warning("Deactivated invalid FCM token: %s", token)
    except Exception:
        logger.exception("Failed to deactivate token: %s", token)


def send_push_notification(token: str, title: str, body: str) -> bool:
    """
    Send a single-message to a single token. Returns True on success.
    """
    message = messaging.Message(
        notification=messaging.Notification(title=title, body=body),
        token=token,
    )
    try:
        resp = messaging.send(message)
        logger.info("✅ Push sent to token %s: %s", token, resp)
        return True
    except Exception as exc:
        logger.exception("❌ send_push_notification failed for token %s: %s", token, exc)
        # Deactivate obviously invalid tokens if message indicates that
        err = str(exc)
        if "registration-token-not-registered" in err or "invalid-registration-token" in err:
            _deactivate_token(token)
        return False


def send_push_to_user(
    users: Union[object, Iterable[object]],
    title: str,
    body: str
) -> Tuple[int, int]:
    """
    Send push notification to a user or to many users.

    Accepts:
      - a single User instance
      - a QuerySet/List/Tuple of User instances

    Returns a tuple: (success_count, failure_count)
    """
    # Normalize input to iterable of users
    if isinstance(users, (list, tuple, QuerySet)):
        user_list = list(users)
    else:
        user_list = [users]

    total_success = 0
    total_failure = 0

    for user in user_list:
        try:
            tokens: List[str] = list(
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

        # Build messaging.Message objects
        messages = [
            messaging.Message(notification=messaging.Notification(title=title, body=body), token=t)
            for t in tokens
        ]

        # Try batch send or fallbacks. Count successes and failures.
        try:
            # Single token -> use send()
            if len(messages) == 1:
                ok = send_push_notification(tokens[0], title, body)
                if ok:
                    total_success += 1
                else:
                    total_failure += 1
                continue

            # Preferred: send_all (newer firebase_admin)
            if hasattr(messaging, "send_all"):
                batch_resp = messaging.send_all(messages)
                # send_all returns object with 'responses' list
                success_count = sum(1 for r in batch_resp.responses if getattr(r, "success", False))
                failure_count = len(batch_resp.responses) - success_count
                total_success += success_count
                total_failure += failure_count

                # Deactivate obvious invalid tokens
                for i, r in enumerate(batch_resp.responses):
                    if not getattr(r, "success", False):
                        exc_str = str(getattr(r, "exception", r))
                        if "registration-token-not-registered" in exc_str or "invalid-registration-token" in exc_str:
                            _deactivate_token(tokens[i])
                        logger.warning("FCM failure for %s token[%d]: %s", user, i, exc_str)
                logger.info("✅ send_all for user %s: success=%d failure=%d", user, success_count, failure_count)
                continue

            # Older SDKs may expose send_each
            if hasattr(messaging, "send_each"):
                send_each_resp = messaging.send_each(messages)
                # best-effort logging of older responses; try to inspect fields
                logger.info("send_each result for %s: %s", user, getattr(send_each_resp, "__dict__", str(send_each_resp)))
                # Best effort to count responses if provided
                try:
                    resp_list = getattr(send_each_resp, "_responses", None) or getattr(send_each_resp, "responses", None)
                    if resp_list:
                        success_count = sum(1 for r in resp_list if getattr(r, "success", False))
                        failure_count = len(resp_list) - success_count
                        total_success += success_count
                        total_failure += failure_count
                except Exception:
                    logger.exception("Could not parse send_each responses for user %s", user)
                continue

            # Final fallback: send one-by-one
            for tkn in tokens:
                ok = send_push_notification(tkn, title, body)
                if ok:
                    total_success += 1
                else:
                    total_failure += 1

        except Exception as exc:
            logger.exception("FCM send_all/send failed for user %s: %s", user, exc)
            # If batch failed entirely, fallback to per-token sends
            for tkn in tokens:
                ok = send_push_notification(tkn, title, body)
                if ok:
                    total_success += 1
                else:
                    total_failure += 1

    return total_success, total_failure
