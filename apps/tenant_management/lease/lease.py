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
import logging
from django.core.exceptions import ValidationError
from django.views import View
logger = logging.getLogger(__name__)







class TenantLeaseCreateView(FormView):
    form_class = CombinedTenantLeaseForm
    template_name = "tenants/partials/tenant_lease_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.unit = get_object_or_404(Unit, pk=kwargs.get("unit_id"))
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        initial.update({
            "unit": self.unit.id,
            "property": self.unit.property.id,
        })
        return initial

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["unit"] = self.unit
        return ctx

    def form_valid(self, form):
        tenant_data = {
            "full_name": form.cleaned_data["full_name"],
            "phone_number": form.cleaned_data["phone_number"],
            "email": form.cleaned_data.get("email"),
            "national_id": form.cleaned_data.get("national_id"),
        }
        lease_data = {
            "unit_id": self.unit.id,
            "start_date": form.cleaned_data["start_date"],
            "deposit_amount": form.cleaned_data.get("deposit_amount", 0),
        }

        try:
            result = TenantLeaseService.create_tenant_with_lease(tenant_data, lease_data)
        except ValidationError as e:
            form.add_error(None, getattr(e, "message", str(e)))
            return self.form_invalid(form)
        except Exception:
            logger.exception("Error creating tenant+lease")
            form.add_error(None, "An unexpected error occurred. Please try again.")
            return self.form_invalid(form)

        # Add a contrib message
        messages.success(self.request, result.get("message", "Tenant and lease created successfully."))

        return JsonResponse({
            "success": True,
            "redirect_url": self.get_success_url()
        })

    def form_invalid(self, form):
        # Return the HTML of the form with errors so JS can update the modal body
        html = self.render_to_string(self.template_name, self.get_context_data(form=form))
        return JsonResponse({
            "success": False,
            "html": html
        }, status=400)

    def get_success_url(self):
        return reverse("property_detail", kwargs={"pk": self.unit.property.pk})

    def render_to_string(self, template_name, context):
        from django.template.loader import render_to_string
        return render_to_string(template_name, context, request=self.request)


    
    




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

