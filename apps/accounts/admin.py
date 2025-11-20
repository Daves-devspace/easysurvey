from django.contrib import admin
from .models import CashbookEntry


@admin.register(CashbookEntry)
class CashbookEntryAdmin(admin.ModelAdmin):
    list_display = (
        "entry_date",
        "created_at",
        "entry_type",
        "amount",
        "balance_after",
        "is_opening_balance",
        "created_by",
    )
    list_filter = (
        "entry_type",
        "is_opening_balance",
        "entry_date",
        "created_at",
        "created_by",
    )
    search_fields = ("description", "created_by__username")
    readonly_fields = (
        "entry_date",
        "created_at",
        "entry_type",
        "amount",
        "balance_after",
        "is_opening_balance",
        "created_by",
        "related_object",
    )
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        """Prevent manual additions via admin (only via API/business logic)."""
        return False

    def has_change_permission(self, request, obj=None):
        """Make rows fully read-only in admin."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Disallow deletion from admin (audit protection)."""
        return False
