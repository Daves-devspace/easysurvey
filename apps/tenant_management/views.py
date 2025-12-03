from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import DetailView, ListView, CreateView, UpdateView, DeleteView, View, FormView
from django.contrib import messages
from django.urls import reverse_lazy
from django.http import JsonResponse
from django.db.models import Count, Prefetch, Q
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.contrib.admin.views.decorators import staff_member_required
from django.views.decorators.http import require_GET
from django.core.paginator import Paginator, EmptyPage
from django.db import transaction
from decimal import Decimal
from datetime import date

from apps.tenant_management.models import Property, Unit, Lease, Tenant, Invoice, Payment, MeterReading, Deposit
from apps.tenant_management.forms import PropertyForm, UnitForm, LeaseForm, TenantCreationForm, PaymentForm
from apps.tenant_management.services.payment_service import PaymentService
from apps.tenant_management.services.billing_cycle_service import BillingCycleService
from apps.tenant_management.services.deposit_service import DepositService
from apps.tenant_management.utils import (
    filter_units_for_property, 
    filter_meter_readings_for_property, 
    get_property_leases_data,
    get_tenant_leases_data
)

# --- JSON API for Select2 ---
@require_GET
def unit_search_json(request):
    """API for searching vacant units."""
    q = request.GET.get('q', '').strip()
    prop_id = request.GET.get('property_id')
    page = int(request.GET.get('page', 1))
    page_size = 20

    qs = Unit.objects.filter(is_occupied=False).select_related('property')
    if prop_id:
        qs = qs.filter(property_id=prop_id)
    if q:
        qs = qs.filter(Q(unit_number__icontains=q) | Q(meter_number__icontains=q))

    paginator = Paginator(qs.order_by('unit_number'), page_size)
    try:
        page_obj = paginator.page(page)
    except EmptyPage:
        return JsonResponse({"results": [], "pagination": {"more": False}})

    results = [{
        "id": u.id,
        "text": f"{u.unit_number} — Rent: {u.rent_amount}",
        "unit_number": u.unit_number
    } for u in page_obj.object_list]

    return JsonResponse({"results": results, "pagination": {"more": page_obj.has_next()}})


# --- Property Views ---

class PropertyListView(ListView):
    model = Property
    template_name = "properties/property_list.html"
    context_object_name = "properties"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['create_form'] = PropertyForm()
        ctx['edit_forms'] = {p.id: PropertyForm(instance=p) for p in ctx['properties']}
        return ctx

class PropertyCreateView(CreateView):
    model = Property
    form_class = PropertyForm
    template_name = "properties/partials/property_form.html"
    success_url = reverse_lazy("property-list")
    def form_valid(self, form):
        messages.success(self.request, "Property created successfully.")
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
    """
    The Dashboard for a specific property.
    Handles 4 Main Tabs: Units, Leases/Billing, Tenants, Meter Readings, Payments.
    """
    model = Property
    template_name = 'properties/property_detail.html'
    context_object_name = 'property_obj'

    def get_queryset(self):
        return Property.objects.annotate(
            units_count=Count('units', distinct=True),
            active_leases_count=Count('units__leases', filter=Q(units__leases__is_active=True), distinct=True),
            active_tenants_count=Count('units__leases__tenant', filter=Q(units__leases__is_active=True), distinct=True),
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        prop = self.object
        
        # --- FIX: Explicitly add counts to context so template can use {{ active_tenants_count }} ---
        ctx['units_count'] = getattr(prop, 'units_count', 0)
        ctx['active_leases_count'] = getattr(prop, 'active_leases_count', 0)
        ctx['active_tenants_count'] = getattr(prop, 'active_tenants_count', 0)

        # 1. Units Tab Data
        status = self.request.GET.get('status', 'all')
        ctx['units'] = filter_units_for_property(prop, status=None if status == 'all' else status)
        
        # 2. Meter Readings Tab Data
        req_month = self.request.GET.get("month")
        today = timezone.now().date()
        default_month = f"{today.year}-{today.month:02d}"
        active_month = req_month or default_month
        
        readings = filter_meter_readings_for_property(prop, month_str=active_month)
        ctx['meter_readings'] = readings
        ctx['active_month'] = active_month
        ctx['reading_status'] = self.request.GET.get("reading_status") or "all"
        
        # --- FIX: Calculate Pending Count for the Main View ---
        ctx['pending_meter_readings_count'] = sum(1 for r in readings if r['status'] == 'pending')
        
        # 3. Leases & Billing Tab Data
        leases_data, aggregates = get_property_leases_data(prop)
        ctx['leases_data'] = leases_data
        ctx['tenants_data'] = leases_data 
        ctx.update(aggregates)

        # 4. Payments Tab Data
        payments_qs = Payment.objects.filter(
            tenant__property=prop
        ).exclude(payment_type='MIXED').select_related('tenant', 'invoice').order_by('-payment_date')
        
        ctx['property_payments'] = payments_qs[:50]
        ctx['payment_active_month'] = active_month 

        # 5. Forms
        ctx['unit_form'] = UnitForm()
        ctx['tenant_form'] = TenantCreationForm()
        ctx['lease_form'] = LeaseForm(initial={'property': prop.id})

        return ctx


class PropertyReadingsPartialView(DetailView):
    """HTMX View: Refreshes ONLY the meter readings table when filters change."""
    model = Property
    template_name = "meter_readings/partials/readings_wrapper.html"
    context_object_name = "property_obj"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        month = self.request.GET.get("month")
        status = self.request.GET.get("reading_status", "all")
        
        readings = filter_meter_readings_for_property(self.object, month_str=month)
        
        # --- OPTIMIZATION: Calculate Pending Count BEFORE filtering ---
        ctx['pending_meter_readings_count'] = sum(1 for r in readings if r['status'] == 'pending')

        if status != "all":
            readings = [r for r in readings if r['status'] == status]
            
        ctx['meter_readings'] = readings
        ctx['active_month'] = month or timezone.now().strftime("%Y-%m")
        ctx['reading_status'] = status
        
        return ctx


class PropertyPaymentsPartialView(DetailView):
    """
    HTMX View: Filters payments for the entire property.
    """
    model = Property
    template_name = "properties/partials/payments_table.html"
    context_object_name = "property_obj"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        prop = self.object
        
        month_str = self.request.GET.get('month')
        p_type = self.request.GET.get('payment_type', 'all')
        
        payments_qs = Payment.objects.filter(
            tenant__property=prop
        ).exclude(payment_type='MIXED').select_related('tenant', 'invoice')

        if month_str:
            try:
                year, month = map(int, month_str.split('-'))
                payments_qs = payments_qs.filter(payment_date__year=year, payment_date__month=month)
            except ValueError:
                pass
        
        if p_type != 'all':
            payments_qs = payments_qs.filter(payment_type__iexact=p_type)

        payments_qs = payments_qs.order_by('-payment_date')

        ctx['property_payments'] = payments_qs
        ctx['payment_active_month'] = month_str
        ctx['current_payment_type'] = p_type
        
        return ctx




# --- Manual Invoice Generation ---
@method_decorator(staff_member_required, name="dispatch")
class ManualInvoiceGenerationView(View):
    def post(self, request, *args, **kwargs):
        date_str = request.POST.get("run_date")
        ref_date = date.fromisoformat(date_str) if date_str else timezone.now().date()
        
        try:
            res = BillingCycleService.generate_rent_roll(target_date=ref_date)
            return JsonResponse({"success": True, "created": res['created'], "ref_date": str(ref_date)})
        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})

@transaction.atomic
def apply_deposit_to_final_invoice(lease, invoice):
    deposit = Deposit.objects.filter(lease=lease, amount_held__gt=0).first()
    if not deposit:
        return None
    
    return DepositService.apply_deposit_to_invoice(
        deposit=deposit,
        invoice=invoice,
        lease=lease,
        amount=deposit.amount_held
    )