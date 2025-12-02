from django.contrib import admin
from .models import FCMToken, Notification

@admin.register(FCMToken)
class FCMTokenAdmin(admin.ModelAdmin):
    list_display = ("user", "token", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("user__username", "token")



@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "is_read", "created_at")
    list_filter = ("is_read", "created_at")
    search_fields = ("title", "message", "user__username", "user__email")
    readonly_fields = ("title", "message", "user", "is_read", "created_at")
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        # Prevent manual creation via admin
        return False        