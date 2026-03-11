from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.core.cache import cache
from .models import Notification, FCMToken
from .serializers import NotificationSerializer, FCMTokenSerializer
from rest_framework.permissions import IsAuthenticated
from django.conf import settings
from django.http import HttpResponse
from django.template import Context, Template
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET
from .firebase_manager import get_active_config

# --- Service Worker Logic ---
SW_SCRIPT_TEMPLATE = """
importScripts('https://www.gstatic.com/firebasejs/9.22.0/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/9.22.0/firebase-messaging-compat.js');

const firebaseConfig = {
  apiKey: "{{ apiKey }}",
  authDomain: "{{ authDomain }}",
  projectId: "{{ projectId }}",
  storageBucket: "{{ storageBucket }}",
  messagingSenderId: "{{ messagingSenderId }}",
  appId: "{{ appId }}"
};

try {
    firebase.initializeApp(firebaseConfig);
} catch(e) {
    console.log("Firebase SW init error (might be already initialized): ", e);
}

const messaging = firebase.messaging();

// Handle background messages
messaging.onBackgroundMessage(function(payload) {
  console.log('[firebase-messaging-sw.js] Received background message ', payload);
  
  const notificationTitle = payload.notification.title;
  const notificationOptions = {
    body: payload.notification.body,
    icon: '/static/images/pages/smrtlg.png', // Update this path to your actual logo
    data: payload.data
  };

  self.registration.showNotification(notificationTitle, notificationOptions);
});
"""

@require_GET
@cache_control(max_age=3600)
def firebase_messaging_sw(request):
    """
    Returns a dynamically generated service worker file.
    """
    # 1. Try DB config first (Robust method)
    db_config = get_active_config()
    
    context_data = {}
    if db_config:
        context_data = {
            'apiKey': db_config.api_key,
            'authDomain': db_config.auth_domain,
            'projectId': db_config.project_id,
            'storageBucket': db_config.storage_bucket,
            'messagingSenderId': db_config.messaging_sender_id,
            'appId': db_config.app_id,
        }
    else:
        # 2. Fallback to settings.py if DB is empty
        config = getattr(settings, 'FIREBASE_CONFIG', {})
        context_data = {
            'apiKey': config.get('apiKey', ''),
            'authDomain': config.get('authDomain', ''),
            'projectId': config.get('projectId', ''),
            'storageBucket': config.get('storageBucket', ''),
            'messagingSenderId': config.get('messagingSenderId', ''),
            'appId': config.get('appId', ''),
        }

    t = Template(SW_SCRIPT_TEMPLATE)
    c = Context(context_data)
    response = HttpResponse(t.render(c), content_type="application/javascript")
    # Critical for scope permissions
    response["Service-Worker-Allowed"] = "/"
    return response


# --- API Views ---

class CombinedNotificationFeedView(APIView):
    """
    Returns:
      - personal: unread notifications for current user (latest 20)
      - monitoring: (superusers only) unseen notifications from other users (latest 20)
      - counts and small user context
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        # Personal unread notifications (latest 20)
        personal_qs = Notification.objects.filter(user=user, is_read=False).order_by('-created_at')[:20]
        personal_data = NotificationSerializer(personal_qs, many=True).data
        personal_count = Notification.objects.filter(user=user, is_read=False).count()

        monitoring_data = []
        monitoring_count = 0

        # Superusers get a monitoring feed of other users' unseen notifications
        if user.is_superuser:
            monitor_qs = Notification.objects.filter(seen_by_admin=False).exclude(user=user).order_by('-created_at')[:20]
            monitoring_data = NotificationSerializer(monitor_qs, many=True).data
            monitoring_count = Notification.objects.filter(seen_by_admin=False).exclude(user=user).count()

            for n in monitoring_data:
                n["superuser_view"] = True
                if "target_user" not in n or n["target_user"] is None:
                    n["target_user"] = None

        current_profile = getattr(user, 'employeeprofile', None)
        if current_profile:
            display = current_profile.display_name if not callable(current_profile.display_name) else current_profile.display_name()
        else:
            display = f"{user.first_name} {user.last_name}".strip() or user.username

        return Response(
            {
                "personal": personal_data,
                "monitoring": monitoring_data,
                "counts": {
                    "personal": personal_count,
                    "monitoring": monitoring_count,
                    "total": personal_count + monitoring_count,
                },
                "user": {
                    "is_superuser": user.is_superuser,
                    "username": user.username,
                    "id": user.id,
                    "display_name": display,
                },
            },
            status=status.HTTP_200_OK,
        )


class MarkNotificationReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        user = request.user
        notification = get_object_or_404(Notification, pk=pk)

        if user.is_superuser:
            notification.seen_by_admin = True
            if notification.user == user:
                notification.is_read = True
                notification.save(update_fields=["seen_by_admin", "is_read"])
                return Response({"status": "marked as seen by admin and read", "id": notification.id}, status=status.HTTP_200_OK)

            notification.save(update_fields=["seen_by_admin"])
            return Response({"status": "marked as seen by admin", "id": notification.id}, status=status.HTTP_200_OK)

        if notification.user == user:
            notification.is_read = True
            notification.save(update_fields=["is_read"])
            return Response({"status": "marked as read", "id": notification.id}, status=status.HTTP_200_OK)

        return Response({"error": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)


class MarkAllAsReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user

        if user.is_superuser:
            if hasattr(Notification, "seen_by_admin"):
                Notification.objects.filter(seen_by_admin=False).update(seen_by_admin=True)
            Notification.objects.filter(user=user, is_read=False).update(is_read=True)
            return Response({"status": "all marked as seen by admin and your personal notifications marked read"}, status=status.HTTP_200_OK)

        Notification.objects.filter(user=user, is_read=False).update(is_read=True)
        return Response({"status": "all marked as read"}, status=status.HTTP_200_OK)


class SaveFCMTokenView(APIView):
    """
    API endpoint to save/update the current user's FCM token.
    AND update the EmployeeProfile status.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        token = str(request.data.get("token") or "").strip()
        if not token:
            return Response({"error": "Token is required"}, status=status.HTTP_400_BAD_REQUEST)

        if len(token) > 255:
            return Response({"error": "Token is too long"}, status=status.HTTP_400_BAD_REQUEST)

        created = False
        changed = False

        # Save only when token ownership/activity actually changes.
        with transaction.atomic():
            existing = FCMToken.objects.select_for_update().filter(token=token).first()
            if existing is None:
                try:
                    FCMToken.objects.create(
                        token=token,
                        user=request.user,
                        is_active=True,
                    )
                    created = True
                except IntegrityError:
                    existing = FCMToken.objects.select_for_update().get(token=token)

            if existing is not None:
                update_fields = []
                if existing.user_id != request.user.id:
                    existing.user = request.user
                    update_fields.append("user")
                if not existing.is_active:
                    existing.is_active = True
                    update_fields.append("is_active")

                if update_fields:
                    existing.save(update_fields=update_fields)
                    changed = True

        # 2. UPDATE PROFILE STATUS (This was missing)
        if hasattr(request.user, 'employeeprofile'):
            profile = request.user.employeeprofile
            # Only update if it wasn't already True to save a DB write
            if not profile.push_notifications_allowed:
                profile.push_notifications_allowed = True
                profile.save(update_fields=['push_notifications_allowed'])

        if created:
            return Response({"message": "✅ Token saved & Profile updated"}, status=status.HTTP_201_CREATED)

        if changed:
            return Response({"message": "ℹ️ Token updated"}, status=status.HTTP_200_OK)

        return Response({"message": "ℹ️ Token already active"}, status=status.HTTP_200_OK)


class AllNotificationsCachedView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_cache_key(self, user):
        if user.is_superuser:
            return "notifications_admin"
        if getattr(user, "role", "") == "manager":
            return "notifications_manager"
        return f"notifications_user_{user.id}"

    def get_queryset(self, user):
        if user.is_superuser or getattr(user, "role", "") == "manager":
            return Notification.objects.select_related("user").order_by("-created_at")
        return Notification.objects.filter(user=user).order_by("-created_at")

    def get(self, request):
        user = request.user
        cache_key = self.get_cache_key(user)
        page = int(request.GET.get("page", 1))
        per_page = int(request.GET.get("length", 25))

        data = cache.get(cache_key)
        if not data:
            queryset = self.get_queryset(user)
            serializer = NotificationSerializer(queryset, many=True)
            data = serializer.data
            cache.set(cache_key, data, timeout=60)

        start = (page - 1) * per_page
        end = start + per_page
        paginated = data[start:end]
        total_records = len(data)

        return Response({
            "data": paginated,
            "recordsTotal": total_records,
            "recordsFiltered": total_records,
            "page": page,
        }, status=status.HTTP_200_OK)