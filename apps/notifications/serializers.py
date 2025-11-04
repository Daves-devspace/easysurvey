# notifications/serializers.py
from rest_framework import serializers
from apps.notifications.models import Notification
from .models import FCMToken

from rest_framework import serializers
from .models import Notification

class NotificationSerializer(serializers.ModelSerializer):
    target_user = serializers.CharField(source='user.username', read_only=True) 
    
    class Meta:
        model = Notification
        fields = ['id', 'title', 'message', 'link', 'is_read', 'created_at', 'target_user']
    
    
    
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