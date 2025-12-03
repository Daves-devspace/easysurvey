from django.contrib import admin
from .models import (
    WaterCompany, Property, WaterRate,
    Unit, Tenant, Lease, MeterReading,
    Invoice, InvoiceLine, Payment, Receipt,
    NotificationLog, TenantBalance, LedgerEntry, Deposit
)


# ------------------------------------------------------------------------------
# Inline Configurations
# ------------------------------------------------------------------------------

class LeaseInline(admin.TabularInline):
    model = Lease
    extra = 0
    fields = ("unit", "start_date", "deposit_amount", "is_active")
    readonly_fields = ("is_active",)
    show_change_link = True


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0
    fields = ("description", "lease", "amount", "line_type")
    show_change_link = True


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    fields = ("amount", "payment_date", "method", "reference")
    show_change_link = True


class LedgerEntryInline(admin.TabularInline):
    model = LedgerEntry
    extra = 0
    fields = ("entry_type", "debit", "credit", "lease", "invoice", "deposit", "description", "created_at")
    readonly_fields = ("created_at",)
    show_change_link = True


# ------------------------------------------------------------------------------
# Main Admin Models
# ------------------------------------------------------------------------------

@admin.register(WaterCompany)
class WaterCompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "contact_info")
    search_fields = ("name",)
    ordering = ("name",)


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = ("name", "location", "water_policy", "water_company", "created_at")
    list_filter = ("water_policy", "water_company")
    search_fields = ("name", "location")
    ordering = ("name",)


@admin.register(WaterRate)
class WaterRateAdmin(admin.ModelAdmin):
    list_display = ("water_company", "rate_per_cubic_meter", "effective_from", "effective_to", "is_active")
    list_filter = ("water_company", "is_active")
    search_fields = ("water_company__name",)
    ordering = ("-effective_from",)


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ("unit_number", "property", "rent_amount", "is_occupied", "meter_number")
    list_filter = ("is_occupied", "property")
    search_fields = ("unit_number", "property__name")
    ordering = ("property", "unit_number")


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone_number", "email", "national_id", "property", "created_at")
    list_filter = ("property",)
    search_fields = ("full_name", "phone_number", "national_id", "email")
    ordering = ("full_name",)
    inlines = [LeaseInline]


@admin.register(Lease)
class LeaseAdmin(admin.ModelAdmin):
    list_display = ("tenant", "unit", "start_date", "deposit_amount", "is_active")
    list_filter = ("is_active", "start_date", "unit__property")
    search_fields = ("tenant__full_name", "unit__unit_number")
    ordering = ("-start_date",)


@admin.register(MeterReading)
class MeterReadingAdmin(admin.ModelAdmin):
    list_display = ("unit", "reading_date", "previous_reading", "current_reading","rate_per_cubic_meter", "usage", "amount")
    list_filter = ("unit__property", "reading_date")
    search_fields = ("unit__unit_number", "unit__property__name")
    ordering = ("-reading_date",)


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "billing_period_start", "billing_period_end", "total_amount", "is_paid")
    list_filter = ("is_paid", "billing_period_start", "tenant__property")
    search_fields = ("tenant__full_name", "tenant__phone_number")
    ordering = ("-billing_period_start",)
    inlines = [InvoiceLineInline, PaymentInline]
    readonly_fields = ("total_amount", "created_at")


@admin.register(InvoiceLine)
class InvoiceLineAdmin(admin.ModelAdmin):
    list_display = ("invoice", "description", "lease", "amount", "line_type")
    list_filter = ("invoice__tenant__property", "line_type")
    search_fields = ("description", "invoice__tenant__full_name")
    ordering = ("invoice",)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("invoice", "amount", "payment_date", "method", "reference")
    list_filter = ("method", "payment_date", "invoice__tenant__property")
    search_fields = ("invoice__tenant__full_name", "reference")
    ordering = ("-payment_date",)
   


@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    list_display = ("receipt_number", "payment", "issued_date")
    search_fields = ("receipt_number", "payment__invoice__tenant__full_name")
    ordering = ("-issued_date",)
    readonly_fields = ("issued_date",)


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ("tenant", "channel", "status", "created_at")
    list_filter = ("channel", "status", "created_at")
    search_fields = ("tenant__full_name", "message")
    ordering = ("-created_at",)


@admin.register(TenantBalance)
class TenantBalanceAdmin(admin.ModelAdmin):
    list_display = ("tenant", "balance")
    search_fields = ("tenant__full_name",)
    ordering = ("tenant",)


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("tenant", "entry_type", "debit", "credit", "lease", "invoice", "deposit", "created_at")
    list_filter = ("entry_type", "created_at")
    search_fields = ("tenant__full_name", "description")
    ordering = ("-created_at",)
    readonly_fields = ("created_at",)


@admin.register(Deposit)
class DepositAdmin(admin.ModelAdmin):
    list_display = ("tenant", "lease", "amount", "amount_held", "paid_at", "refunded_at", "refunded_amount")
    list_filter = ( "paid_at", "refunded_at")
    search_fields = ("tenant__full_name", "lease__unit__unit_number")
    ordering = ("-paid_at",)
    readonly_fields = ("amount_held", "refunded_amount")
    inlines = [LedgerEntryInline]
