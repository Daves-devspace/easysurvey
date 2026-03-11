from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.core.cache import cache
import logging
from .models import FCMToken, Notification
from .tasks import send_pending_push_notifications

logger = logging.getLogger(__name__)

@receiver(post_save, sender=FCMToken)
def flush_pending_on_token_save(sender, instance, created, **kwargs):
    try:
        update_fields = kwargs.get("update_fields")
        updated_fields = set(update_fields) if update_fields is not None else set()

        should_flush = (
            instance.is_active and (
                created or
                (update_fields is not None and bool(updated_fields & {"is_active", "user"}))
            )
        )
        if not should_flush:
            return

        debounce_key = f"notifications:pending-flush:queued:{instance.user_id}"
        if not cache.add(debounce_key, "1", timeout=20):
            logger.debug("Pending flush already queued recently for user %s", instance.user_id)
            return

        logger.info("FCM token saved for user %s — triggering pending push flush", instance.user_id)
        send_pending_push_notifications.delay(instance.user_id)
    except Exception:
        logger.exception("Error in flush_pending_on_token_save")

def _invalidate_cache_for_notification(notification):
    user = notification.user
    cache_keys = [
        f"notifications_user_{user.id}",
        "notifications_admin",
        "notifications_manager",
    ]
    for key in cache_keys:
        cache.delete(key)

@receiver(post_save, sender=Notification)
def clear_cache_on_save(sender, instance, **kwargs):
    _invalidate_cache_for_notification(instance)

@receiver(post_delete, sender=Notification)
def clear_cache_on_delete(sender, instance, **kwargs):
    _invalidate_cache_for_notification(instance)