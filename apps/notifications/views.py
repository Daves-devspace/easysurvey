from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
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

# This template defines the Service Worker logic. 
# It uses Django template syntax {{ }} to inject variables.
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

firebase.initializeApp(firebaseConfig);

const messaging = firebase.messaging();

// Handle background messages
messaging.onBackgroundMessage(function(payload) {
  console.log('[firebase-messaging-sw.js] Received background message ', payload);
  
  const notificationTitle = payload.notification.title;
  const notificationOptions = {
    body: payload.notification.body,
    icon: '/static/images/logo.png', // Update this path to your actual logo
    data: payload.data
  };

  self.registration.showNotification(notificationTitle, notificationOptions);
});
"""

@require_GET
@cache_control(max_age=3600)  # Cache for 1 hour to reduce server load
def firebase_messaging_sw(request):
    """
    Returns a dynamically generated service worker file.
    This allows us to inject the specific Docker container's Firebase config
    into the client-side browser worker.
    """
    config = getattr(settings, 'FIREBASE_CONFIG', {})
    
    # Render the JS string with the config from settings
    t = Template(SW_SCRIPT_TEMPLATE)
    c = Context({
        'apiKey': config.get('apiKey', ''),
        'authDomain': config.get('authDomain', ''),
        'projectId': config.get('projectId', ''),
        'storageBucket': config.get('storageBucket', ''),
        'messagingSenderId': config.get('messagingSenderId', ''),
        'appId': config.get('appId', ''),
    })
    
    # Return as Javascript content type
    return HttpResponse(t.render(c), content_type="application/javascript")


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

            # mark each item as viewed through superuser context (no extra DB queries)
            for n in monitoring_data:
                n["superuser_view"] = True
                # target_user is already provided by the serializer (display_name or fallback)
                if "target_user" not in n or n["target_user"] is None:
                    # defensive fallback - should rarely be needed
                    n["target_user"] = None

        # include current user small context with display name for better frontend UX
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
    """
    Marks a single notification as read/seen.

    Rules:
      - If a superuser calls this endpoint: mark `seen_by_admin=True` for monitoring purposes.
        * If the notification belongs to the superuser themself, also mark `is_read=True` so
          their personal count drops.
      - If a regular user calls this endpoint and they own the notification: mark `is_read=True`.
      - Otherwise return 403.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        user = request.user
        notification = get_object_or_404(Notification, pk=pk)

        # Superuser action: mark item as seen by admin for monitoring
        if user.is_superuser:
            notification.seen_by_admin = True

            # If the notification belongs to the superuser, also mark it as read
            if notification.user == user:
                notification.is_read = True
                notification.save(update_fields=["seen_by_admin", "is_read"])
                return Response(
                    {"status": "marked as seen by admin and read", "id": notification.id},
                    status=status.HTTP_200_OK,
                )

            # It belongs to someone else — only mark seen_by_admin
            notification.save(update_fields=["seen_by_admin"])
            return Response(
                {"status": "marked as seen by admin", "id": notification.id},
                status=status.HTTP_200_OK,
            )

        # Regular user: can only mark their own notifications as read
        if notification.user == user:
            notification.is_read = True
            notification.save(update_fields=["is_read"])
            return Response(
                {"status": "marked as read", "id": notification.id},
                status=status.HTTP_200_OK,
            )

        return Response({"error": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)


class MarkAllAsReadView(APIView):
    """
    Marks all notifications as read for the current user.

    Behavior:
      - Superusers: mark *all* unseen monitoring items as `seen_by_admin=True` (the monitoring feed)
        and also mark the superuser's personal notifications as `is_read=True` so their badge clears.
      - Regular users: mark all their `is_read=False` notifications as `is_read=True`.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user

        if user.is_superuser:
            # mark monitoring items as seen by admin
            if hasattr(Notification, "seen_by_admin"):
                Notification.objects.filter(seen_by_admin=False).update(seen_by_admin=True)

            # also mark the superuser's personal notifications as read so the badge clears
            Notification.objects.filter(user=user, is_read=False).update(is_read=True)

            return Response(
                {"status": "all marked as seen by admin and your personal notifications marked read"},
                status=status.HTTP_200_OK,
            )

        # regular user
        Notification.objects.filter(user=user, is_read=False).update(is_read=True)
        return Response({"status": "all marked as read"}, status=status.HTTP_200_OK)


class SaveFCMTokenView(APIView):
    """
    API endpoint to save/update the current user's FCM token.

    Behavior:
      - Expects POST JSON body: { "token": "<fcm-token>" }
      - Uses update_or_create to be idempotent (no duplicate tokens)
      - Associates the token to the authenticated user
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        token = request.data.get("token")
        if not token:
            return Response({"error": "Token is required"}, status=status.HTTP_400_BAD_REQUEST)

        # Idempotent save: update user for existing token, create new otherwise
        obj, created = FCMToken.objects.update_or_create(token=token, defaults={"user": request.user})

        if created:
            return Response({"message": "✅ Token saved successfully"}, status=status.HTTP_201_CREATED)

        return Response({"message": "ℹ️ Token already exists, user updated"}, status=status.HTTP_200_OK)




class AllNotificationsCachedView(APIView):
    """
    Returns notifications from Redis cache.
    - Admin/Manager: see all notifications.
    - Regular users: only see their own.
    - Cached for performance, invalidated automatically via signals.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get_cache_key(self, user):
        """Generate cache key per role/user."""
        if user.is_superuser:
            return "notifications_admin"
        if getattr(user, "role", "") == "manager":
            return "notifications_manager"
        return f"notifications_user_{user.id}"

    def get_queryset(self, user):
        """Role-based queryset."""
        if user.is_superuser or getattr(user, "role", "") == "manager":
            return Notification.objects.select_related("user").order_by("-created_at")
        return Notification.objects.filter(user=user).order_by("-created_at")

    def get(self, request):
        user = request.user
        cache_key = self.get_cache_key(user)
        page = int(request.GET.get("page", 1))
        per_page = int(request.GET.get("length", 25))  # DataTables uses 'length'

        # Try Redis cache first
        data = cache.get(cache_key)
        if not data:
            # Cache miss → query + serialize
            queryset = self.get_queryset(user)
            serializer = NotificationSerializer(queryset, many=True)
            data = serializer.data
            cache.set(cache_key, data, timeout=60)  # cache for 1 minute

        # Manual pagination
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