from django.shortcuts         import get_object_or_404, redirect
from django.urls             import reverse
from django.views.generic    import CreateView
from django.contrib.messages import success

from apps.tenant_management.models import Unit, Lease
from apps.tenant_management.forms  import LeaseForm

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.views.generic import CreateView, ListView, DetailView
from django.views.generic.edit import FormView
from django.urls import reverse, reverse_lazy
from django.http import JsonResponse
from django.db.models import Prefetch, Q

from apps.tenant_management.forms import (
    TenantCreationForm,
    LeaseCreationForm,
    CombinedTenantLeaseForm
)
from .services import TenantLeaseService
from apps.tenant_management.models import Tenant, Lease, Unit, Property
from django.template.loader import render_to_string
from django.http import HttpResponse, HttpResponseBadRequest


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





class TenantLeaseCreateView(FormView):
    """
    HTMX-enabled view for creating both Tenant and Lease in one step
    (e.g., from a vacant unit row in PropertyDetailView).
    """
    form_class    = CombinedTenantLeaseForm
    template_name = 'tenants/partials/tenant_lease_form.html'
    # fallback if someone browses directly
    success_url   = None  

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        unit_id = self.kwargs.get("unit_id")
        ctx["unit"]       = get_object_or_404(Unit, pk=unit_id)
        return ctx

    def get_initial(self):
        initial = super().get_initial()
        unit = get_object_or_404(Unit, pk=self.kwargs['unit_id'])
        initial['unit']     = unit.id
        initial['property'] = unit.property.id
        return initial

    def get_form_kwargs(self):
        # Remove instance so plain Form doesn’t error
        kwargs = super().get_form_kwargs()
        kwargs.pop('instance', None)
        return kwargs

    def form_valid(self, form):
        # Build data dicts
        tenant_data = {
            'full_name':    form.cleaned_data['full_name'],
            'phone_number': form.cleaned_data['phone_number'],
            'email':        form.cleaned_data['email'],
            'national_id':  form.cleaned_data['national_id'],
        }
        lease_data = {
            'unit_id':        form.cleaned_data['unit'].id,
            'start_date':     form.cleaned_data['start_date'],
            'deposit_amount': form.cleaned_data['deposit_amount'],
        }

        # Call service
        try:
            result = TenantLeaseService.create_tenant_with_lease(tenant_data, lease_data)
        except Exception as e:
            # HTMX: return error fragment
            if self.request.headers.get('HX-Request'):
                return HttpResponseBadRequest(f"Error: {e}")
            messages.error(self.request, f"Error: {e}")
            return self.form_invalid(form)

        # On success
        unit = result['lease'].unit

        # HTMX swap: re-render just the unit row
        if self.request.headers.get('HX-Request'):
            html = render_to_string(
                'units/partials/unit_row.html',
                {'unit': unit},
                request=self.request
            )
            return HttpResponse(html)

        # Non-HTMX: redirect back to property_detail
        messages.success(self.request, result['message'])
        return redirect(self.get_success_url(unit))

    def get_success_url(self, unit):
        """
        Returns the property_detail URL for the given unit's property.
        """
        return reverse('property_detail', kwargs={'pk': unit.property.pk})

    def form_invalid(self, form):
        # HTMX: re-render form with errors
        if self.request.headers.get('HX-Request'):
            print("FORM ERRORS:", form.errors)  # For debugging
            ctx = self.get_context_data(form=form)
            html = render_to_string(self.template_name, ctx, request=self.request)
            return HttpResponse(html, status=400)

        # Non-HTMX fallback
        messages.error(self.request, 'Please correct the errors below.')
        return self.render_to_response(self.get_context_data(form=form))


    
    


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


class LeaseListView(ListView):
    """
    Paginated list of leases with filters for status and property.
    """
    model = Lease
    template_name = 'leases/lease_list.html'
    context_object_name = 'leases'
    paginate_by = 20

    def get_queryset(self):
        qs = Lease.objects.select_related('tenant', 'unit__property').order_by('-start_date')
        status = self.request.GET.get('status')
        if status == 'active':
            qs = qs.filter(is_active=True)
        elif status == 'inactive':
            qs = qs.filter(is_active=False)
        prop = self.request.GET.get('property')
        if prop:
            qs = qs.filter(unit__property_id=prop)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['status_filter'] = self.request.GET.get('status', '')
        ctx['property_filter'] = self.request.GET.get('property', '')
        ctx['properties'] = Property.objects.all()
        return ctx


class LeaseDetailView(DetailView):
    """
    Detailed view of a Lease, showing tenant, unit, and related invoices/payments.
    """
    model = Lease
    template_name = 'leases/lease_detail.html'
    context_object_name = 'lease'

    def get_queryset(self):
        return Lease.objects.select_related(
            'tenant', 'unit__property'
        ).prefetch_related('invoices__payments__receipt')


# AJAX endpoint to dynamically load units for a selected property

def get_units_by_property(request):
    property_id = request.GET.get('property_id')
    units = []
    if property_id:
        units = TenantLeaseService.get_available_units(property_id)
    data = [{'id': u.id, 'text': f"{u.unit_number} - Ksh {u.rent_amount}"} for u in units]
    return JsonResponse({'units': data})


def end_lease_view(request, lease_id):
    """
    End a lease (via POST), free the unit, then redirect back to lease detail.
    """
    if request.method == 'POST':
        try:
            result = TenantLeaseService.end_lease_and_free_unit(lease_id)
            if result.get('success'):
                messages.success(request, result['message'])
        except Exception as e:
            messages.error(request, str(e))
    return redirect('lease_detail', pk=lease_id)

