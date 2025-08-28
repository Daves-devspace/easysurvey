from django.contrib import admin
from .models import (
    Property, Unit, Tenant, Lease, 
    MeterReading, Invoice, Payment, 
    Receipt, NotificationLog
)


class UnitInline(admin.TabularInline):
    model = Unit
    extra = 1
    show_change_link = True


class TenantInline(admin.TabularInline):
    model = Tenant
    extra = 1
    show_change_link = True


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = ("name", "location", "water_policy", "water_rate", "created_at")
    search_fields = ("name", "location")
    list_filter = ("water_policy", "created_at")
    inlines = [UnitInline, TenantInline]
    ordering = ("-created_at",)


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ("unit_number", "property", "rent_amount", "is_occupied", "meter_number")
    search_fields = ("unit_number", "property__name", "meter_number")
    list_filter = ("is_occupied", "property")
    autocomplete_fields = ("property",)
    ordering = ("property", "unit_number")


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone_number", "property", "created_at")
    search_fields = ("full_name", "phone_number", "national_id", "property__name")
    list_filter = ("property",)
    autocomplete_fields = ("property",)
    ordering = ("-created_at",)


class InvoiceInline(admin.TabularInline):
    model = Invoice
    extra = 0
    readonly_fields = ("total_amount", "is_paid")
    show_change_link = True


@admin.register(Lease)
class LeaseAdmin(admin.ModelAdmin):
    list_display = ("tenant", "unit", "start_date", "deposit_amount", "is_active")
    search_fields = ("tenant__full_name", "unit__unit_number", "unit__property__name")
    list_filter = ("is_active", "start_date", "tenant__property")
    autocomplete_fields = ("tenant", "unit")
    inlines = [InvoiceInline]


@admin.register(MeterReading)
class MeterReadingAdmin(admin.ModelAdmin):
    list_display = ("unit", "reading_date", "previous_reading", "current_reading", "usage", "amount")
    search_fields = ("unit__unit_number", "unit__property__name")
    list_filter = ("reading_date", "unit__property")
    autocomplete_fields = ("unit",)
    readonly_fields = ("usage", "amount")
    ordering = ("-reading_date",)


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    readonly_fields = ("payment_date", "balance_after", "method")
    show_change_link = True


@admin.action(description="Mark selected invoices as paid")
def mark_invoices_paid(modeladmin, request, queryset):
    for invoice in queryset:
        invoice.mark_paid()


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("id", "lease", "invoice_date", "due_date", "total_amount", "is_paid")
    search_fields = ("lease__tenant__full_name", "lease__unit__unit_number")
    list_filter = ("is_paid", "invoice_date", "due_date")
    autocomplete_fields = ("lease",)
    readonly_fields = ("total_amount", "auto_generated")
    inlines = [PaymentInline]
    actions = [mark_invoices_paid]
    ordering = ("-invoice_date",)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("amount", "tenant", "invoice", "payment_date", "method", "balance_after")
    search_fields = ("tenant__full_name", "invoice__id", "mpesa_receipt")
    list_filter = ("method", "payment_date", "tenant__property")
    autocomplete_fields = ("tenant", "invoice")
    readonly_fields = ("balance_after", "payment_date")
    ordering = ("-payment_date",)


@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    list_display = ("receipt_number", "payment", "issued_date")
    search_fields = ("receipt_number", "payment__tenant__full_name", "payment__invoice__id")
    readonly_fields = ("issued_date",)
    ordering = ("-issued_date",)


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ("tenant", "channel", "status", "created_at")
    search_fields = ("tenant__full_name", "message")
    list_filter = ("channel", "status", "created_at")
    autocomplete_fields = ("tenant",)
    ordering = ("-created_at",)
