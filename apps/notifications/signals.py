# apps/notifications/signals.py
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import FCMToken
from .tasks import send_pending_push_notifications
import logging
from apps.notifications.models import Notification
from django.core.cache import cache

logger = logging.getLogger(__name__)


@receiver(post_save, sender=FCMToken)
def flush_pending_on_token_save(sender, instance, created, **kwargs):
    """
    When a token becomes active (created or updated to active),
    enqueue task to send any pending notifications for that user.
    """
    try:
        if instance.is_active:
            logger.info("FCM token saved for user %s — triggering pending push flush", instance.user_id)
            send_pending_push_notifications.delay(instance.user_id)
    except Exception:
        logger.exception("Error in flush_pending_on_token_save")
        
        
def _invalidate_cache_for_notification(notification):
    """Invalidate cache for affected users and roles."""
    user = notification.user
    cache_keys = [
        f"notifications_user_{user.id}",
        "notifications_admin",
        "notifications_manager",
    ]

    for key in cache_keys:
        cache.delete(key)
        print(f"🔄 Redis cache invalidated for key: {key}")

@receiver(post_save, sender=Notification)
def clear_cache_on_save(sender, instance, **kwargs):
    """Clear cache when a notification is created or updated."""
    _invalidate_cache_for_notification(instance)

@receiver(post_delete, sender=Notification)
def clear_cache_on_delete(sender, instance, **kwargs):
    """Clear cache when a notification is deleted."""
    _invalidate_cache_for_notification(instance)