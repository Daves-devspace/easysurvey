# apps/tenants/admin.py
"""
Django Admin configuration for multi-tenant models.
"""

from django.contrib import admin
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
        'user_count',
        'created_on',
        'paid_until',
    )
    list_filter = (
        'is_active',
        'plan',
        ('paid_until', admin.EmptyFieldListFilter),
        'created_on',
    )
    search_fields = (
        'name',
        'slug',
        'admin_email',
        'email',
    )
    readonly_fields = (
        'uuid',
        'created_on',
        'updated_on',
        'schema_name',
        'is_on_trial',
        'is_expired',
        'days_until_expiry',
    )
    fieldsets = (
        ('Identity', {
            'fields': ('name', 'slug', 'uuid')
        }),
        ('Database & Schema', {
            'fields': ('schema_name',),
            'description': 'Automatically generated - this is the PostgreSQL schema name'
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
                'email',
                'phone',
                'website',
            )
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
    )
    
    def display_name(self, obj):
        """Display company name with icon."""
        return f"[Company] {obj.name}"
    display_name.short_description = 'Company'
    
    def plan_badge(self, obj):
        """Display plan with color badge."""
        colors = {
            'starter': '#0066cc',      # Blue
            'professional': '#00aa00',  # Green
            'enterprise': '#ff6600',    # Orange
        }
        color = colors.get(obj.plan, '#666')
        plan_display = obj.get_plan_display()
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; '
            'border-radius: 3px; font-size: 11px; font-weight: bold;">{}</span>',
            color,
            plan_display
        )
    plan_badge.short_description = 'Plan'
    
    def status_badge(self, obj):
        """Display active/inactive status with badge."""
        if obj.is_active:
            return format_html(
                '<span style="background-color: #00aa00; color: white; padding: 3px 8px; '
                'border-radius: 3px; font-size: 11px; font-weight: bold;">Active</span>'
            )
        else:
            return format_html(
                '<span style="background-color: #cc0000; color: white; padding: 3px 8px; '
                'border-radius: 3px; font-size: 11px; font-weight: bold;">Suspended</span>'
            )
    status_badge.short_description = 'Status'
    
    def user_count(self, obj):
        """Display current user count vs limit."""
        # This would query the tenant schema - simplified for now
        return f"? / {obj.max_users}"
    user_count.short_description = 'Users'
    
    actions = ['activate_tenants', 'deactivate_tenants']
    
    def activate_tenants(self, request, queryset):
        """Bulk action to activate tenants."""
        count = queryset.update(is_active=True)
        self.message_user(request, f'{count} tenant(s) activated.')
    activate_tenants.short_description = 'Activate selected tenants'
    
    def deactivate_tenants(self, request, queryset):
        """Bulk action to deactivate tenants."""
        count = queryset.update(is_active=False)
        self.message_user(request, f'{count} tenant(s) deactivated.')
    deactivate_tenants.short_description = 'Deactivate selected tenants'


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
        'domain',
    )
    fieldsets = (
        ('Domain', {
            'fields': ('domain',)
        }),
        ('Tenant Assignment', {
            'fields': ('tenant', 'is_primary')
        }),
        ('Metadata', {
            'fields': ('created_on',),
            'classes': ('collapse',)
        }),
    )
    raw_id_fields = ('tenant',)
    
    def tenant_link(self, obj):
        """Display tenant name as a link to the tenant's admin page."""
        url = reverse('admin:tenants_company_change', args=[obj.tenant.pk])
        return format_html(
            '<a href="{}">{}</a>',
            url,
            obj.tenant.name
        )
    tenant_link.short_description = 'Tenant'
    
    def is_primary_badge(self, obj):
        """Display primary/secondary domain status."""
        if obj.is_primary:
            return format_html(
                '<span style="background-color: #00aa00; color: white; padding: 3px 8px; '
                'border-radius: 3px; font-size: 11px; font-weight: bold;">Primary</span>'
            )
        else:
            return format_html(
                '<span style="background-color: #cccccc; color: black; padding: 3px 8px; '
                'border-radius: 3px; font-size: 11px;">Secondary</span>'
            )
    is_primary_badge.short_description = 'Type'
