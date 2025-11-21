from django.shortcuts import get_object_or_404
from django.shortcuts import redirect, render
from django.urls import reverse_lazy, reverse
from django.views.generic import DetailView, ListView, CreateView, UpdateView, DeleteView
from django.views.generic.base import TemplateResponseMixin
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from .models import Property, Unit, Lease, Tenant, Payment, Invoice, Deposit, MeterReading
from .forms import PropertyForm, UnitForm,LeaseForm, CombinedTenantLeaseForm, TenantCreationForm
import json
from django.http import HttpResponseRedirect, HttpResponseBadRequest,Http404, JsonResponse
from django.db.models import Count, Prefetch, Sum, Q,OuterRef,Subquery,DecimalField, Value
from decimal import Decimal
from apps.tenant_management.utils import filter_units_for_property,filter_meter_readings_for_property,get_property_leases_data
from django.db.models import F, ExpressionWrapper

from apps.tenant_management.services.deposit_service import DepositService
from django.db.models.functions import Coalesce
from django.db import transaction, IntegrityError

from typing import Optional
from datetime import datetime




from django.core.paginator import Paginator, EmptyPage
from django.views.decorators.http import require_GET

@require_GET
def unit_search_json(request):
    """
    JSON endpoint used by the Select2 AJAX widget.
    Params:
      - property_id (optional but in tenant-detail case we'll pass it)
      - q (search text)
      - page (1-based)
      - page_size (optional, default 20)
    Response: {
      "results": [{"id": 123, "text": "UnitNumber — rent 1200"}, ...],
      "pagination": {"more": True/False}
    }
    """
    q = request.GET.get('q', '').strip()
    prop_id = request.GET.get('property_id')
    page = int(request.GET.get('page', 1))
    page_size = int(request.GET.get('page_size', 20))

    qs = Unit.objects.filter(is_occupied=False).select_related('property')

    if prop_id:
        qs = qs.filter(property_id=prop_id)

    if q:
        # search unit_number or meter_number or property name
        qs = qs.filter(
            Q(unit_number__icontains=q) |
            Q(meter_number__icontains=q) |
            Q(property__name__icontains=q)
        )

    qs = qs.order_by('unit_number')  # stable ordering

    paginator = Paginator(qs, page_size)
    try:
        page_obj = paginator.page(page)
    except EmptyPage:
        return JsonResponse({"results": [], "pagination": {"more": False}})

    results = [
        {
        "id": u.id,
        "text": f"{u.unit_number} — {u.property.name} (rent {u.rent_amount})",
        "unit_number": u.unit_number,
        "rent": str(u.rent_amount),
        "meter_number": u.meter_number or '',
        "property_name": u.property.name
        } for u in page_obj.object_list
    ]


    return JsonResponse({
        "results": results,
        "pagination": {"more": page_obj.has_next()}
    })



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
        active_leases_qs = (
            Lease.objects.filter(is_active=True)
            .select_related('tenant', 'unit')
        )

        return (
            Property.objects
            .annotate(
                units_count=Count('units', distinct=True),
                active_leases_count=Count(
                    'units__leases',
                    filter=Q(units__leases__is_active=True),
                    distinct=True,
                ),
                active_tenants_count=Count(
                    'units__leases__tenant',
                    filter=Q(
                        units__leases__is_active=True,
                        units__leases__tenant__isnull=False,
                    ),
                    distinct=True,
                ),
            )
            .prefetch_related(
                'units',
                Prefetch(
                    'units__leases',
                    queryset=active_leases_qs,
                    to_attr='active_leases_prefetched'
                ),
            )
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        property_obj = self.object

        ctx['property_obj'] = property_obj
        ctx['property'] = property_obj

        # --- Units list (for unit-specific table/filters) ---
        status = self.request.GET.get('status')
        if status == 'all':
            status = None
        ctx['units'] = filter_units_for_property(property_obj, status=status)

        # --- Meter readings (for meter readings table) ---
        month = self.request.GET.get("month")

        # if no month provided, use last full month (billing is retrospective)
        today = datetime.today().date()
        if today.month == 1:
            default_year, default_month = today.year - 1, 12
        else:
            default_year, default_month = today.year, today.month - 1

        default_month_str = f"{default_year}-{default_month:02d}"
        active_month = month or default_month_str

        ctx['meter_readings'] = filter_meter_readings_for_property(property_obj, month_str=active_month)

        # --- Aggregates & counters ---
        ctx['units_count'] = getattr(property_obj, 'units_count', property_obj.units.count())
        ctx['active_leases_count'] = getattr(property_obj, 'active_leases_count', 0)
        ctx['active_tenants_count'] = getattr(property_obj, 'active_tenants_count', 0)

        # --- Forms ---
        ctx['unit_form'] = UnitForm()
        ctx['tenant_form'] = TenantCreationForm()
        ctx['lease_form'] = LeaseForm(initial={'property': property_obj.id})

        # --- Lease/tenant aggregates ---
        leases_data, aggregates = get_property_leases_data(property_obj)
        ctx['leases_data'] = leases_data
        ctx['tenants_data'] = leases_data
        ctx.update(aggregates)

        # --- State ---
        ctx['total_units'] = len(list(ctx['units']))
        ctx['current_status'] = status or 'all'
        ctx['current_month'] = month or ""
        ctx['active_month'] = active_month  

        # --- Pending readings count ---
        ctx['pending_meter_readings_count'] = sum(
            1 for r in ctx['meter_readings'] if r['status'] == 'pending'
        )
        
        ctx['reading_status'] = self.request.GET.get("reading_status") or "all"

        return ctx




class PropertyReadingsPartialView(DetailView):
    """HTMX endpoint to render only readings table + pending flag."""
    model = Property
    template_name = "meter_readings/partials/readings_wrapper.html"  
    context_object_name = "property_obj"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        property_obj = self.object

        # --- Filtering ---
        month = self.request.GET.get("month")
        status_filter = self.request.GET.get("reading_status")

        meter_readings = filter_meter_readings_for_property(property_obj, month_str=month)

        if status_filter and status_filter != "all":
            meter_readings = [r for r in meter_readings if r["status"] == status_filter]

        # --- Pending counts ---
        today = datetime.today().date()
        default_month = f"{today.year}-{today.month:02d}"
        active_month = month or default_month
        pending_count = sum(1 for r in meter_readings if r["status"] == "pending")

        ctx.update({
            "meter_readings": meter_readings,
            "pending_meter_readings_count": pending_count,
            "active_month": active_month,
            "reading_status": status_filter or "all",
        })
        return ctx





@transaction.atomic
def apply_deposit_to_final_invoice(lease, invoice):
    """
    Apply deposit to final invoice at lease end
    """
    deposit = Deposit.objects.filter(lease=lease, amount_held__gt=0).first()
    if not deposit:
        return None
    
    return DepositService.apply_deposit_to_invoice(
        deposit=deposit,
        invoice=invoice,
        lease=lease,
        amount=deposit.amount_held  # Apply the full amount
    )