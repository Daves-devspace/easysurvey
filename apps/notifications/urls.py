from django.urls import path
from .views import CombinedNotificationFeedView, MarkNotificationReadView, MarkAllAsReadView, SaveFCMTokenView, AllNotificationsCachedView


urlpatterns = [
    path('api/notifications/', CombinedNotificationFeedView.as_view(), name='notifications'),
    path('api/notifications/<int:pk>/mark-read/', MarkNotificationReadView.as_view(), name='notification_mark_read'),
    path('api/notifications/mark-all-read/', MarkAllAsReadView.as_view(), name='notifications_mark_all_read'),
    path("api/save-fcm-token/", SaveFCMTokenView.as_view(), name="save_fcm_token"),
    path('all/', AllNotificationsCachedView.as_view(), name='notifications_all'),
]