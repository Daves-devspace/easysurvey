from django.db import models
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
#  Import from local .fields 
from .fields import EncryptedTextField 
import json

User = get_user_model()

class FirebaseConfig(models.Model):
    """
    Singleton model to store Firebase configuration.
    Uses EncryptedTextField for the private JSON key.
    """
    # Public Settings (Safe to be plain text, needed by JS)
    api_key = models.CharField(max_length=255)
    auth_domain = models.CharField(max_length=255, blank=True, null=True)
    project_id = models.CharField(max_length=255)
    storage_bucket = models.CharField(max_length=255, blank=True, null=True)
    messaging_sender_id = models.CharField(max_length=255)
    app_id = models.CharField(max_length=255)
    vapid_key = models.CharField(max_length=255)

    # Private Credentials (ENCRYPTED IN DB)
    service_account_json = EncryptedTextField(
        blank=True,
        help_text="Paste your firebase-service-account.json content here. It will be encrypted on save."
    )
    
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Firebase Configuration"
        verbose_name_plural = "Firebase Configuration"

    def save(self, *args, **kwargs):
        # Validation: Decrypt occurs automatically on access, so we can just read it
        try:
            if self.service_account_json:
                json.loads(self.service_account_json)
        except json.JSONDecodeError:
            raise ValidationError("Invalid JSON. Please copy the file content exactly.")
        
        # Singleton Logic
        if self.is_active:
            FirebaseConfig.objects.filter(is_active=True).exclude(pk=self.pk).update(is_active=False)
            
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Firebase Config ({'Active' if self.is_active else 'Inactive'}) - {self.project_id}"


class FCMToken(models.Model):
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
    seen_by_admin = models.BooleanField(default=False)
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
    sent_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user} — {self.title}"