from django.conf import settings
from django.urls import path, include
from django.views.generic import DetailView, CreateView, ListView
from django.db.models import Prefetch, Q
from django.shortcuts import render
from apps.tenant_management.models import Tenant, Lease, Payment, Invoice
from apps.tenant_management.forms import TenantCreationForm, CombinedTenantLeaseForm
from django.contrib import messages
from django.urls import reverse_lazy

class TenantCreateView(CreateView):
    """
    Standalone form to create a Tenant.
    """
    model = Tenant
    form_class = TenantCreationForm
    template_name = 'tenants/create_tenant.html'
    success_url = reverse_lazy('tenant_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f'Tenant "{self.object.full_name}" created successfully!')
        return response

    def form_invalid(self, form):
        messages.error(self.request, 'Please correct the errors below.')
        return super().form_invalid(form)


class TenantListView(ListView):
    """
    Paginated list of tenants with optional search.
    """
    model = Tenant
    template_name = 'tenants/tenant_list.html'
    context_object_name = 'tenants'
    paginate_by = 20

    def get_queryset(self):
        qs = Tenant.objects.prefetch_related(
            Prefetch('leases', queryset=Lease.objects.select_related('unit__property'))
        ).order_by('-created_at')
        search = self.request.GET.get('search')
        if search:
            qs = qs.filter(
                Q(full_name__icontains=search) |
                Q(phone_number__icontains=search) |
                Q(national_id__icontains=search) |
                Q(email__icontains=search)
            )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['search_query'] = self.request.GET.get('search', '')
        return ctx



class TenantDetailView(DetailView):
    model = Tenant
    template_name = "tenants/tenant_detail.html"
    context_object_name = "tenant"
    pk_url_kwarg = "tenant_id"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        tenant = self.object

        # Fetch all leases for this tenant
        leases = Lease.objects.filter(tenant=tenant).select_related('unit', 'unit__property')

        # Fetch all payments for this tenant
        payments = Payment.objects.filter(tenant=tenant).select_related('invoice', 'invoice__lease').order_by("-payment_date")

        # Prepare leases data with balances and meter readings
        leases_data = []
        for lease in leases:
            invoices = Invoice.objects.filter(lease=lease)
            total_rent = lease.unit.rent_amount
            total_paid = sum(p.amount for p in Payment.objects.filter(invoice__in=invoices, tenant=tenant))
            balance = total_rent - total_paid
            last_meter = lease.unit.meter_readings.last()

            leases_data.append({
                "lease": lease,
                "unit": lease.unit,
                "property": lease.unit.property,
                "rent_amount": lease.unit.rent_amount,
                "deposit": lease.deposit_amount,
                "status": "Active" if lease.is_active else "Expired",
                "balance": balance,
                "previous_meter": getattr(last_meter, "previous_reading", None),
                "current_meter": getattr(last_meter, "current_reading", None),
            })

        # Aggregate totals
        total_deposit = sum(ld["deposit"] for ld in leases_data)
        total_paid = sum(p.amount for p in payments)
        total_balance = sum(ld["balance"] for ld in leases_data)

        # Add to context
        ctx.update({
            "leases": leases,
            "payments": payments,
            "leases_data": leases_data,
            "total_units": leases.count(),
            "total_deposit": total_deposit,
            "total_paid": total_paid,
            "total_balance": total_balance,
        })

        return ctx