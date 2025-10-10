from django.contrib import admin

from .forms import ClientServiceForm
from .models import (Client, Service, Process, ClientService,ClientSubService, ClientServiceProcess, Payment, Document, DocType,
                     SmsProviderToken, ClientDoc, TitleDeedCollection, SiteSettings, ScheduledTask, AuditLog,DriveOAuthToken)


from django.utils.timezone import now
import logging
from django.contrib import admin
from .models import SiteSettings
from .files.utils import get_connection_status
from django.utils.html import format_html
from apps.EasyDocs.files.utils import _build_service_from_oauth
from django.urls import reverse
from google.auth.exceptions import RefreshError


logger = logging.getLogger(__name__)


@admin.register(DriveOAuthToken)
class DriveOAuthTokenAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "status_badge",
        "token_expiry",
        "updated_at",
        "refresh_token_button",
    )
    readonly_fields = (
        "refresh_token_encrypted",
        "access_token_encrypted",
        "token_expiry",
        "scopes",
        "created_at",
        "updated_at",
    )

    def status_badge(self, obj):
        """
        Display a small color-coded badge:
        - Green = valid token
        - Red = expired token
        """
        if obj.token_expiry and obj.token_expiry < now():
            color = "red"
            text = "Expired"
        else:
            color = "green"
            text = "Valid"
        return format_html('<span style="color: {};">● {}</span>', color, text)

    status_badge.short_description = "Token Status"

    def refresh_token_button(self, obj):
        return format_html(
            '<a class="button" href="{}">Refresh Token</a>',
            f"/admin/drive-oauth-token/refresh/{obj.id}/"
        )
    refresh_token_button.short_description = "Manual Refresh"
    refresh_token_button.allow_tags = True


# Admin view to refresh token
from django.urls import path
from django.shortcuts import redirect, get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required

@staff_member_required
def refresh_drive_token(request, token_id):
    """
    Admin helper to manually refresh the single company OAuth token.
    """
    token_obj = get_object_or_404(DriveOAuthToken, pk=token_id)
    try:
        _, creds = _build_service_from_oauth()  # Will refresh automatically and update DB
        messages.success(
            request,
            f"Company OAuth token refreshed successfully. Expiry: {creds.expiry}",
        )
        logger.info("Admin manually refreshed company OAuth token id=%s", token_id)
    except RefreshError:
        messages.error(request, "Token is expired or revoked — re-authorization required.")
        logger.error(
            "Admin attempted refresh but token is expired/revoked id=%s", token_id
        )
    except Exception as e:
        messages.error(request, f"Failed to refresh token: {e}")
        logger.exception(
            "Unexpected error during manual token refresh id=%s", token_id
        )

    # Redirect back to the token list in admin
    return redirect("/admin/apps/easydocs/driveoauthtoken/")

# Register the URL with admin
def get_admin_urls(urls):
    my_urls = [
        path("drive-oauth-token/refresh/<int:token_id>/", refresh_drive_token, name="refresh_drive_token"),
    ]
    return my_urls + urls

admin.site.get_urls = lambda urls=admin.site.get_urls(): get_admin_urls(urls)



@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "timestamp",
        "user",
        "action",
        "model_name",
        "object_id",
        "short_description",
        "ip_address",
    )
    list_filter = ("action", "model_name", "timestamp")
    search_fields = ("user__username", "model_name", "description", "ip_address")
    readonly_fields = (
        "user",
        "action",
        "model_name",
        "object_id",
        "description",
        "ip_address",
        "user_agent",
        "timestamp",
    )
    ordering = ("-timestamp",)

    def short_description(self, obj):
        """Truncate long descriptions in list view for readability."""
        if obj.description:
            return (obj.description[:50] + "...") if len(obj.description) > 50 else obj.description
        return "-"
    short_description.short_description = "Description"

    def has_add_permission(self, request):
        # Prevent manual creation
        return False

    def has_change_permission(self, request, obj=None):
        # Prevent edits — logs should be immutable
        return False


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    """
    All your site‐wide display settings live here,
    but the `email` field is pulled in read‐only from EmailSettings.
    """
   
    list_display = ("id", "company_name", "google_drive_enabled", "connection_status_badge", "updated_at")
    readonly_fields = ("connection_status_badge", "email")

    fieldsets = (
        (None, {
            "fields": (
                "company_name",
                "logo",
                "company_email",
                "company_phone",
                "tagline",
                "stamp_signature",
                "google_drive_enabled",
                "google_oauth_client_id",
                "google_oauth_client_secret_encrypted",
                "google_drive_root_folder_id",
                "google_drive_service_account_key_encrypted",
                "google_oauth_redirect_uris",
                "connection_status_badge",   # <-- read-only badge
            )
        }),
    )

    def email(self, obj):
        # Always show from EmailSettings (readonly)
        return obj.email

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def connection_status_badge(self, obj):
        """
        Display Drive connection status as a colored badge, with storage mode (OAuth vs Service Account).
        """
        status = get_connection_status(obj)

        css_class = status.get("class", "warning")
        message = status.get("message", "Unknown")

        # Add mode (OAuth vs Service Account) to message if known
        mode_display = status.get("storage_mode_display", "")
        if mode_display and mode_display != "Unknown":
            message = f"{message} ({mode_display})"

        color_map = {
            "success": "#28a745",   # green
            "warning": "#ffc107",   # yellow
            "error": "#dc3545",     # red
            "info": "#17a2b8",      # blue
        }
        bg_color = color_map.get(css_class, "#6c757d")  # default gray

        return format_html(
            '<span style="padding:4px 8px; border-radius:6px; '
            'color:#fff; background:{}; font-weight:bold;">{}</span>',
            bg_color, message
        )

    connection_status_badge.short_description = "Drive Connection Status"


# Inline for Processes in ClientService
class ClientServiceProcessInline(admin.TabularInline):
    model = ClientServiceProcess
    extra = 0
    readonly_fields = ('process', 'status', 'completed_at')

# Inline for Payments in ClientService
class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0

# Inline for ClientService in Client
class ClientServiceInline(admin.TabularInline):
    model = ClientService
    extra = 0
    readonly_fields = ('service', 'requested_at', 'total_paid', 'total_balance')
    inlines = [ClientServiceProcessInline, PaymentInline]
    
    
@admin.register(ClientSubService)
class ClientSubServiceAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'client_service',
        'sub_service',
        'price',
        'paid_amount',
        'balance',
        'is_paid_to_legal_office',
        'paid_month',
        'paid_at',
        'added_on',
    )
    list_filter = (
        'is_paid_to_legal_office',
        'paid_month',
        'added_on',
        'sub_service',
    )
    search_fields = (
        'client_service__id',
        'client_service__client__name',  # assumes ClientService has FK to Client with "name"
        'sub_service__name',
    )
    readonly_fields = (
        'institution_cost_snapshot',
        'overridden_price_snapshot',
        'paid_at',
        'added_on',
    )
    date_hierarchy = 'added_on'

    fieldsets = (
        ("Links", {
            "fields": ("client_service", "sub_service"),
        }),
        ("Financials", {
            "fields": ("overridden_price", "paid_amount", "institution_cost_snapshot", "overridden_price_snapshot"),
        }),
        ("Payout Tracking", {
            "fields": ("is_paid_to_legal_office", "paid_month", "paid_at"),
        }),
        ("Metadata", {
            "fields": ("added_on",),
        }),
    )
    
    
    
    
@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ('first_name','last_name', 'email', 'phone')
    search_fields = ('first_name','last_name', 'email', 'phone')
    inlines = [ClientServiceInline]

@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'total_price', 'has_processes')
    list_filter = ('category',)
    search_fields = ('name', 'description')

    def has_processes(self, obj):
        return obj.processes.exists()
    has_processes.boolean = True
    has_processes.short_description = 'Has Processes?'


@admin.register(Process)
class ProcessAdmin(admin.ModelAdmin):
    list_display = ('service', 'name', 'step_order', 'cost', 'short_message')
    list_filter = ('service__category', 'service')
    search_fields = ('name', 'description', 'message')

    def short_message(self, obj):
        return (obj.message[:40] + '...') if len(obj.message) > 40 else obj.message
    short_message.short_description = 'Message'


from django.contrib import admin
from .models import Booking

@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ['id', 'client_service']  # customize as needed
    search_fields = ['client_service__id']



class TitleDeedCollectionInline(admin.StackedInline):
    model = TitleDeedCollection
    extra = 0
    max_num = 1
    can_delete = False
    readonly_fields = ('collected_at',)



@admin.register(ClientService)
class ClientServiceAdmin(admin.ModelAdmin):
    form = ClientServiceForm
    list_display = ('client', 'service', 'land_description', 'status', 'requested_at','total_paid', 'total_balance')
    search_fields = ('client__first_name', 'client__last_name', 'land_description')
    list_filter = ( 'service','land_description','status')
    inlines = [ClientServiceProcessInline, PaymentInline, TitleDeedCollectionInline]

@admin.register(TitleDeedCollection)
class TitleDeedCollectionAdmin(admin.ModelAdmin):
    list_display = ('client_service', 'collected_by', 'id_number', 'phone_number', 'collected_at','message')
    search_fields = ('collected_by', 'id_number', 'phone_number', 'client_service__client__first_name')
    list_filter = ('collected_at',)



@admin.register(ClientServiceProcess)
class ClientServiceProcessAdmin(admin.ModelAdmin):
    list_display = ('client_service', 'process', 'status', 'completed_at')
    list_filter = ('status', 'client_service')

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('client_service', 'amount', 'payment_method', 'transaction_id', 'payment_date','institution_cost_snapshot','overridden_total_snapshot')
    list_filter = ('payment_method',)

@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ('doc_name', 'doc_type', 'location', 'reference', 'uploaded_at')

@admin.register(DocType)
class DocTypeAdmin(admin.ModelAdmin):
    list_display = ('name',)

@admin.register(ClientDoc)
class ClientDocAdmin(admin.ModelAdmin):
    list_display = ('client', 'doc_name', 'doc_type', 'uploaded_at')
    search_fields = ('client__first_name', 'doc_name')

@admin.register(SmsProviderToken)
class SmsProviderTokenAdmin(admin.ModelAdmin):
    list_display = ('api_token', 'sender_id')


from django.contrib import admin, messages
from django.db.models import Q, Sum, F
from .models import LegalOfficePayout, ClientSubService


@admin.action(description="🔁 Relink missing subservices to selected payout(s)")
def relink_missing_subservices(modeladmin, request, queryset):
    total_linked = 0
    payout_count = 0

    for payout in queryset:
        # Find subservices marked paid, with matching month, but not linked
        missing_subs = ClientSubService.objects.filter(
            is_paid_to_legal_office=True,
            paid_month=payout.month
        ).exclude(legalofficepayouts=payout)

        if missing_subs.exists():
            payout.subservices.add(*missing_subs)

            # Recalculate and update total
            missing_total = missing_subs.aggregate(total=Sum('paid_amount'))['total'] or 0
            payout.total_amount = F('total_amount') + missing_total
            payout.save()

            total_linked += missing_subs.count()
            payout_count += 1

    if total_linked > 0:
        messages.success(
            request,
            f"✅ Relinked {total_linked} subservice(s) across {payout_count} payout(s)."
        )
    else:
        messages.info(request, "ℹ️ No missing subservices found to relink.")


@admin.register(LegalOfficePayout)
class LegalOfficePayoutAdmin(admin.ModelAdmin):
    list_display = ("month", "total_amount")
    actions = [relink_missing_subservices]



@admin.register(ScheduledTask)
class ScheduledTaskAdmin(admin.ModelAdmin):
    list_display = ("task_name", "scheduled_time", "status", "created_at")
    readonly_fields = ("task_id", "task_name", "scheduled_time", "created_at")
    list_filter = ("status",)
