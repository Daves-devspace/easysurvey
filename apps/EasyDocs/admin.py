from django.contrib import admin
from .models import (Client, Service, Process, ClientService, ClientServiceProcess, Payment, Document, DocType,
                     SmsProviderToken, ClientDoc, TitleDeedCollection, SiteSettings)




@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        # Prevent adding more than one
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        # Prevent deletion
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
    list_display = ('name', 'total_price')

@admin.register(Process)
class ProcessAdmin(admin.ModelAdmin):
    list_display = ('service', 'name', 'step_order', 'cost')
    list_filter = ('service',)

class TitleDeedCollectionInline(admin.StackedInline):
    model = TitleDeedCollection
    extra = 0
    max_num = 1
    can_delete = False
    readonly_fields = ('collected_at',)



@admin.register(ClientService)
class ClientServiceAdmin(admin.ModelAdmin):
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
