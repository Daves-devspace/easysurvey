# notifications/serializers.py
from rest_framework import serializers
from apps.notifications.models import Notification
from .models import FCMToken

from rest_framework import serializers
from .models import Notification

class NotificationSerializer(serializers.ModelSerializer):
    target_user = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            'id', 'title', 'message', 'link',
            'is_read', 'created_at', 'target_user'
        ]

    def get_target_user(self, obj):
        """Return the display name or fallback for the notification sender."""
        user = obj.user
        profile = getattr(user, 'employeeprofile', None)

        # If profile has display_name (property or callable), use it
        if profile:
            display = getattr(profile, 'display_name', None)
            if callable(display):
                try:
                    return display()
                except Exception:
                    pass
            if display:
                return display

        # fallback: show full name or username
        full_name = f"{user.first_name} {user.last_name}".strip()
        return full_name or user.username

    
    
class FCMTokenSerializer(serializers.ModelSerializer):
    """
    Serializer that validates and saves a user's FCM token.
    Automatically associates it with the logged-in user.
    """
    class Meta:
        model = FCMToken
        fields = ["token"]

    def create(self, validated_data):
        user = self.context["request"].user
        token = validated_data["token"]

        obj, created = FCMToken.objects.update_or_create(
            user=user,
            token=token,
            defaults={"is_active": True}
        )
        return obj