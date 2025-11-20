from django.db import models
from django.conf import settings
from django.contrib.auth import get_user_model
User = get_user_model()

class FCMToken(models.Model):
    """
    Stores an FCM (Firebase Cloud Messaging) token for each user.
    One user may have multiple tokens (e.g., different devices or browsers).
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="fcm_tokens"
    )
    token = models.CharField(max_length=255, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} - {self.token[:25]}..."

class Notification(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='notifications'
    )
    title = models.CharField(max_length=255)
    message = models.TextField()
    link = models.URLField(
        max_length=500,
        blank=True,
        null=True,
        help_text="Optional link to redirect when clicked."
    )
    is_read = models.BooleanField(default=False)
    seen_by_admin = models.BooleanField(default=False)  #
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} -> {self.user.username}"

    def mark_as_read(self):
        self.is_read = True
        self.save(update_fields=['is_read'])
        
        
class PendingPushNotification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    sent = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.user} — {self.title}"