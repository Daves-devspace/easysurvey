from django.contrib import admin

from .forms import ClientServiceForm
from .models import (Client, Service, Process, ClientService, ClientServiceProcess, Payment, Document, DocType,
                     SmsProviderToken, ClientDoc, TitleDeedCollection, SiteSettings)




from django.contrib import admin
from .models import SiteSettings


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    """
    All your site‐wide display settings live here,
    but the `email` field is pulled in read‐only from EmailSettings.
    """
    readonly_fields = ('email',)
    # Order/group the fields you want to edit:
    fields = (
        'company_name',
        'logo',
        'email',
        'phone',
        'tagline',
        'stamp_signature',
    )

    def email(self, obj):
        # This property accessor pulls from EmailSettings,
        # so it always shows the current default_from_email.
        return obj.email

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


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
    list_display = ('client_service', 'amount', 'payment_method', 'transaction_id', 'payment_date')
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
