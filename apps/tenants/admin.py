# apps/tenants/admin.py
"""
Django Admin configuration for multi-tenant models.

Safety rules enforced here:
  - The public tenant (schema_name="public") can NEVER be deleted or archived via admin.
  - The "Delete" action is replaced with "Archive (soft-delete)" everywhere.
  - A "Restore" bulk action un-archives soft-deleted tenants.
  - The list shows ALL tenants including archived ones (via objects_with_deleted queryset).
"""

from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.utils.html import format_html
from django.urls import reverse
from django.db.models import Count
from .models import Company, Domain


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    """Admin interface for managing Companies (Tenants)."""

    list_display = (
        'display_name',
        'slug',
        'plan_badge',
        'status_badge',
        'archived_badge',
        'user_count',
        'created_on',
        'paid_until',
        'deleted_at',
    )
    list_filter = (
        'is_active',
        'plan',
        ('deleted_at', admin.EmptyFieldListFilter),
        ('paid_until', admin.EmptyFieldListFilter),
        'created_on',
    )
    search_fields = (
        'name',
        'slug',
        'admin_email',
        'email',
        'schema_name',
    )
    readonly_fields = (
        'uuid',
        'schema_name',
        'created_on',
        'updated_on',
        'is_on_trial',
        'is_expired',
        'days_until_expiry',
        'deleted_at',
        'deleted_by',
        'support_access_updated_by',
    )
    fieldsets = (
        ('Identity', {
            'fields': ('name', 'slug', 'uuid')
        }),
        ('Database & Schema', {
            'fields': ('schema_name',),
            'description': 'Automatically generated — this is the PostgreSQL schema name'
        }),
        ('Status', {
            'fields': (
                'is_active',
                'is_on_trial',
                'is_expired',
                'days_until_expiry',
            )
        }),
        ('Subscription', {
            'fields': (
                'plan',
                'paid_until',
                'trial_period_days',
            )
        }),
        ('Limits (by plan)', {
            'fields': (
                'max_users',
                'max_clients',
                'max_storage_gb',
            ),
            'classes': ('collapse',)
        }),
        ('Contact Information', {
            'fields': (
                'admin_email',
                'admin_name',
                'bootstrap_it_email',
                'bootstrap_it_name',
                'email',
                'phone',
                'website',
            )
        }),
        ('Support Privacy', {
            'fields': (
                'support_access_mode',
                'support_access_until',
                'support_access_reason',
                'support_access_updated_by',
            ),
            'description': 'Tenant privacy controls for vendor IT Support access.',
        }),
        ('Company Details', {
            'fields': (
                'logo',
                'description',
            ),
            'classes': ('collapse',)
        }),
        ('Internal', {
            'fields': (
                'notes',
                'created_on',
                'updated_on',
            ),
            'classes': ('collapse',)
        }),
        ('Archive / Soft-Delete', {
            'fields': (
                'deleted_at',
                'deleted_by',
                'deletion_reason',
            ),
            'classes': ('collapse',),
            'description': (
                'When deleted_at is set, this tenant is archived. '
                'All its PostgreSQL schema data is still intact. '
                'Use the "Restore" action to recover it.'
            ),
        }),
    )

    # ── Managers ─────────────────────────────────────────────────────────────

    def get_queryset(self, request):
        """Show ALL tenants (including archived) in admin so admins can restore them."""
        return Company.objects_with_deleted.annotate(payment_count=Count('subscription_payments'))

    # ── Delete protection ─────────────────────────────────────────────────────

    def has_delete_permission(self, request, obj=None):
        """
        Block hard-deletion entirely through the Django admin UI.
        The only delete path is soft-delete via the archive action.
        """
        return False

    def delete_model(self, request, obj):
        """Safety net: route any delete attempt to soft_delete instead."""
        if obj.schema_name == 'public':
            self.message_user(request, 'The public tenant cannot be archived.', level=messages.ERROR)
            return
        obj.soft_delete(user=request.user, reason='Archived from Django admin')
        self.message_user(request, f'Tenant "{obj.name}" has been archived (soft-deleted).', level=messages.WARNING)

    def delete_queryset(self, request, queryset):
        """Route bulk delete to soft_delete for each tenant."""
        count = 0
        skipped = 0
        for obj in queryset:
            if obj.schema_name == 'public':
                skipped += 1
                continue
            obj.soft_delete(user=request.user, reason='Bulk archived from Django admin')
            count += 1
        if count:
            self.message_user(request, f'{count} tenant(s) archived (soft-deleted).', level=messages.WARNING)
        if skipped:
            self.message_user(request, f'{skipped} tenant(s) skipped (public tenant is protected).', level=messages.ERROR)

    # ── Bulk actions ──────────────────────────────────────────────────────────

    actions = ['activate_tenants', 'deactivate_tenants', 'archive_tenants', 'restore_tenants']

    def activate_tenants(self, request, queryset):
        count = queryset.filter(deleted_at__isnull=True).update(is_active=True)
        self.message_user(request, f'{count} tenant(s) activated.')
    activate_tenants.short_description = 'Activate selected tenants'

    def deactivate_tenants(self, request, queryset):
        count = queryset.exclude(schema_name='public').filter(deleted_at__isnull=True).update(is_active=False)
        self.message_user(request, f'{count} tenant(s) deactivated.', level=messages.WARNING)
    deactivate_tenants.short_description = 'Deactivate (suspend) selected tenants'

    def archive_tenants(self, request, queryset):
        """Soft-delete selected tenants (excluding public)."""
        count = 0
        for obj in queryset.exclude(schema_name='public').filter(deleted_at__isnull=True):
            obj.soft_delete(user=request.user, reason='Bulk archived from Django admin')
            count += 1
        self.message_user(request, f'{count} tenant(s) archived.', level=messages.WARNING)
    archive_tenants.short_description = '🗄 Archive (soft-delete) selected tenants'

    def restore_tenants(self, request, queryset):
        """Restore soft-deleted tenants back to active."""
        count = 0
        for obj in queryset.filter(deleted_at__isnull=False):
            obj.restore()
            count += 1
        self.message_user(request, f'{count} tenant(s) restored to active.')
    restore_tenants.short_description = '♻ Restore archived tenants'

    # ── Display helpers ───────────────────────────────────────────────────────

    def display_name(self, obj):
        icon = '🗄' if obj.is_archived else '🏢'
        return format_html('{} {}', icon, obj.name)
    display_name.short_description = 'Company'

    def plan_badge(self, obj):
        colors = {
            'starter': '#0066cc',
            'professional': '#00aa00',
            'enterprise': '#ff6600',
        }
        color = colors.get(obj.plan, '#666')
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 7px;border-radius:3px;font-size:11px;font-weight:bold">{}</span>',
            color, obj.get_plan_display()
        )
    plan_badge.short_description = 'Plan'

    def status_badge(self, obj):
        if obj.is_active:
            return format_html('<span style="background:#00aa00;color:#fff;padding:2px 7px;border-radius:3px;font-size:11px;font-weight:bold">Active</span>')
        return format_html('<span style="background:#cc0000;color:#fff;padding:2px 7px;border-radius:3px;font-size:11px;font-weight:bold">Suspended</span>')
    status_badge.short_description = 'Status'

    def archived_badge(self, obj):
        if obj.is_archived:
            return format_html(
                '<span style="background:#888;color:#fff;padding:2px 7px;border-radius:3px;font-size:11px;font-weight:bold" title="Archived by {} on {}">Archived</span>',
                obj.deleted_by or '?',
                obj.deleted_at.strftime('%Y-%m-%d') if obj.deleted_at else '',
            )
        return format_html('<span style="color:#999;font-size:11px">—</span>')
    archived_badge.short_description = 'Archived'

    def user_count(self, obj):
        return f'? / {obj.max_users}'
    user_count.short_description = 'Users'


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    """Admin interface for managing Domains."""

    list_display = (
        'domain',
        'tenant_link',
        'is_primary_badge',
        'created_on',
    )
    list_filter = (
        'is_primary',
        ('tenant__plan', admin.RelatedOnlyFieldListFilter),
        ('tenant__is_active', admin.RelatedOnlyFieldListFilter),
    )
    search_fields = (
        'domain',
        'tenant__name',
        'tenant__slug',
    )
    readonly_fields = (
        'created_on',
    )
    fieldsets = (
        ('Domain', {
            'fields': ('domain',)
        }),
        ('Tenant Assignment', {
            'fields': ('tenant',)
        }),
        ('Metadata', {
            'fields': ('created_on',),
            'classes': ('collapse',)
        }),
    )
    raw_id_fields = ('tenant',)

    def get_queryset(self, request):
        """Show domains for all tenants, including archived ones."""
        return Domain.objects.select_related('tenant').filter(
            tenant__in=Company.objects_with_deleted.all()
        )

    def has_add_permission(self, request):
        """Domain creation happens during tenant bootstrap/onboarding only."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Deleting the only domain would break tenant routing, so block it."""
        return False

    def save_model(self, request, obj, form, change):
        obj.is_primary = True
        try:
            super().save_model(request, obj, form, change)
        except ValidationError as exc:
            self.message_user(request, exc.messages[0], level=messages.ERROR)
            raise

    def tenant_link(self, obj):
        url = reverse('admin:tenants_company_change', args=[obj.tenant.pk])
        return format_html('<a href="{}">{}</a>', url, obj.tenant.name)
    tenant_link.short_description = 'Tenant'

    def is_primary_badge(self, obj):
        if obj.is_primary:
            return format_html('<span style="background:#00aa00;color:#fff;padding:2px 7px;border-radius:3px;font-size:11px;font-weight:bold">Primary</span>')
        return format_html('<span style="background:#ccc;color:#333;padding:2px 7px;border-radius:3px;font-size:11px">Secondary</span>')
    is_primary_badge.short_description = 'Type'


