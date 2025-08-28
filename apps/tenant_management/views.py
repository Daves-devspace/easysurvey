from django.shortcuts import get_object_or_404
from django.shortcuts import redirect, render
from django.urls import reverse_lazy, reverse
from django.views.generic import DetailView, ListView, CreateView, UpdateView, DeleteView
from django.views.generic.base import TemplateResponseMixin
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from .models import Property, Unit, Lease, Tenant, Payment, Invoice
from .forms import PropertyForm, UnitForm,LeaseForm, CombinedTenantLeaseForm, TenantCreationForm
import json
from django.http import HttpResponseRedirect, HttpResponseBadRequest,Http404, JsonResponse
from django.db.models import Count, Prefetch, Sum, Q,OuterRef,Subquery,DecimalField, Value
from decimal import Decimal
from .services import get_property_leases_data
from django.db.models.functions import Coalesce
from django.db import transaction, IntegrityError
from apps.tenant_management.utils import filter_units_for_property




# mixin to pick HTMX template
class HTMXTemplateResponseMixin(TemplateResponseMixin):
    def render_to_response(self, context, **resp_kw):
        if self.request.headers.get("HX-Request"):
            tpl = getattr(self, "template_name_hx", self.template_name)
            return self.response_class(self.request, tpl, context, **resp_kw)
        return super().render_to_response(context, **resp_kw)



class PropertyListView(ListView):
    model = Property
    template_name = "properties/property_list.html"
    context_object_name = "properties"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['create_form'] = PropertyForm()
        # ensure 'property' always exists in template context
        #context['property'] = None 
        # Create a dict of property.id → PropertyForm(instance=property)
        context['edit_forms'] = {
            prop.id: PropertyForm(instance=prop) for prop in context['properties']
        }
        return context
    
    
class PropertyCreateView(CreateView):
    model = Property
    form_class = PropertyForm
    template_name = "properties/partials/property_form.html"
    success_url = reverse_lazy("property-list")

    def form_valid(self, form):
        messages.success(self.request, "Property added.")
        return super().form_valid(form)

class PropertyUpdateView(UpdateView):
    model = Property
    form_class = PropertyForm
    template_name = "properties/partials/property_form.html"
    success_url = reverse_lazy("property-list")

    def form_valid(self, form):
        messages.success(self.request, "Property updated.")
        return super().form_valid(form)

class PropertyDeleteView(DeleteView):
    model = Property
    template_name = "properties/partials/property_confirm_delete.html"
    success_url = reverse_lazy("property-list")

    def delete(self, request, *args, **kwargs):
        prop = self.get_object()
        messages.success(request, f"{prop.name} deleted.")
        return super().delete(request, *args, **kwargs)
    
    
    
    






class PropertyDetailView(DetailView):
    model = Property
    template_name = 'properties/property_detail.html'
    context_object_name = 'property_obj'

    def get_queryset(self):
        # keep your annotations as-is
        return (
            Property.objects
            .annotate(
                units_count=Count('units', distinct=True),
                active_leases_count=Count(
                    'units__lease',
                    filter=Q(units__lease__is_active=True),
                    distinct=True
                ),
                active_tenants_count=Count(
                    'units__lease__tenant',
                    filter=Q(units__lease__is_active=True, units__lease__tenant__isnull=False),
                    distinct=True
                )
            )
            .prefetch_related('units')
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        property_obj = self.object

        ctx['property_obj'] = property_obj
        ctx['property'] = property_obj

        # Get status from request (if any). property detail may be loaded with ?status=occupied via HTMX
        status = self.request.GET.get('status')
        if status == 'all':
            status = None

        # Use the shared util - this will annotate units with has_active_lease
        ctx['units'] = filter_units_for_property(property_obj, status=status)
        ctx['units_count'] = getattr(property_obj, 'units_count', property_obj.units.count())
        ctx['active_leases_count'] = getattr(property_obj, 'active_leases_count', 0)
        ctx['active_tenants_count'] = getattr(property_obj, 'active_tenants_count', 0)

        ctx['unit_form'] = UnitForm()
        ctx['tenant_form'] = TenantCreationForm()
        ctx['lease_form'] = LeaseForm(initial={'property': property_obj.id})

        leases_data, aggregates = get_property_leases_data(property_obj)
        ctx['leases_data'] = leases_data
        ctx['tenants_data'] = leases_data  # legacy
        ctx.update(aggregates)

        # totals for template convenience
        ctx['total_units'] = len(list(ctx['units']))
        return ctx



