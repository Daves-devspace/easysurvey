from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import DetailView, ListView, CreateView, UpdateView, DeleteView, View, FormView
from django.contrib import messages
from django.urls import reverse_lazy
from django.http import JsonResponse
from django.db.models import Count, Prefetch, Q, F
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.contrib.admin.views.decorators import staff_member_required
from django.views.decorators.http import require_GET
from django.core.paginator import Paginator, EmptyPage
from django.db import transaction
from decimal import Decimal
from datetime import date
from apps.tenant_management.models import NotificationLog
from apps.tenant_management.models import Property, Unit, Lease, Tenant, Invoice, Payment, MeterReading, Deposit,WaterCompany,WaterRate
from apps.tenant_management.forms import PropertyForm, UnitForm, LeaseForm, TenantCreationForm, PaymentForm, AnnouncementForm, BulkCommunicationForm
from apps.tenant_management.services.payment_service import PaymentService
from apps.tenant_management.services.billing_cycle_service import BillingCycleService
from apps.tenant_management.services.deposit_service import DepositService
from apps.tenant_management.utils import (
    filter_units_for_property, 
    filter_meter_readings_for_property, 
    get_property_leases_data,
    get_tenant_leases_data
)
from django.template.loader import render_to_string
from django.http import HttpResponse

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
        ctx['water_companies'] = WaterCompany.objects.annotate(
            active_rate=F('water_rates__rate_per_cubic_meter')
        ).filter(water_rates__is_active=True) | WaterCompany.objects.all()
        ctx['water_companies'] = WaterCompany.objects.all().prefetch_related('water_rates')
        ctx['water_rates'] = WaterRate.objects.select_related('water_company').order_by('-is_active', '-effective_from') 
         #: Global Communication Logs (Recent 50)
        ctx['global_comm_logs'] = NotificationLog.objects.select_related('tenant', 'tenant__property').order_by('-created_at')[:50]
        ctx['bulk_comm_form'] = BulkCommunicationForm()
        return ctx

# --- UPDATED: Property CRUD Views ---
class PropertyCreateView(CreateView):
    model = Property
    form_class = PropertyForm
    template_name = "properties/partials/property_form.html"
    success_url = reverse_lazy("property-list")

    def form_valid(self, form):
        self.object = form.save()
        messages.success(self.request, "Property created successfully.")
        
        # If HTMX, signal a page refresh to show the new property
        if self.request.headers.get('HX-Request'):
            return HttpResponse(status=204, headers={'HX-Refresh': 'true'})
            
        return super().form_valid(form)

    def form_invalid(self, form):
        # If HTMX, render the form with errors inside the modal
        if self.request.headers.get('HX-Request'):
            return render(self.request, self.template_name, {'form': form})
        
        return super().form_invalid(form)

class PropertyUpdateView(UpdateView):
    model = Property
    form_class = PropertyForm
    template_name = "properties/partials/property_form.html"
    success_url = reverse_lazy("property-list")

    def form_valid(self, form):
        self.object = form.save()
        messages.success(self.request, "Property updated.")
        
        if self.request.headers.get('HX-Request'):
            return HttpResponse(status=204, headers={'HX-Refresh': 'true'})
            
        return super().form_valid(form)

    def form_invalid(self, form):
        if self.request.headers.get('HX-Request'):
            return render(self.request, self.template_name, {'form': form, 'object': self.object})
        
        return super().form_invalid(form)



class PropertyDeleteView(DeleteView):
    model = Property
    template_name = "properties/partials/property_confirm_delete.html"
    success_url = reverse_lazy("property-list")

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        prop_id = self.object.id
        
        try:
            self.object.delete()
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    "success": True, 
                    "message": "Property deleted.",
                    "row_id": f"property-row-{prop_id}"
                })
            messages.success(request, f"Property deleted.")
            return redirect(self.success_url)
            
        except Exception as e:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({"success": False, "error": str(e)}, status=500)
            messages.error(request, f"Error deleting property: {e}")
            return redirect(self.success_url)




class PropertyDetailView(DetailView):
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
        
        ctx['units_count'] = getattr(prop, 'units_count', 0)
        ctx['active_leases_count'] = getattr(prop, 'active_leases_count', 0)
        ctx['active_tenants_count'] = getattr(prop, 'active_tenants_count', 0)

        # 1. Units
        status = self.request.GET.get('status', 'all')
        ctx['units'] = filter_units_for_property(prop, status=None if status == 'all' else status)
        
        # 2. Readings
        req_month = self.request.GET.get("month")
        today = timezone.now().date()
        default_month = f"{today.year}-{today.month:02d}"
        active_month = req_month or default_month
        readings = filter_meter_readings_for_property(prop, month_str=active_month)
        ctx['meter_readings'] = readings
        ctx['active_month'] = active_month
        ctx['reading_status'] = self.request.GET.get("reading_status") or "all"
        ctx['pending_meter_readings_count'] = sum(1 for r in readings if r['status'] == 'pending')
        
        # 3. Leases
        leases_data, aggregates = get_property_leases_data(prop)
        ctx['leases_data'] = leases_data
        ctx['tenants_data'] = leases_data 
        ctx.update(aggregates)

        # 4. Payments
        # Base Query: Exclude allocation children (show receipts)
        payments_qs = Payment.objects.filter(
            tenant__property=prop
        ).exclude(reference__startswith="Allocation from").select_related('tenant', 'invoice').order_by('-payment_date')
        
        # FIX: Apply type filter if present in URL
        current_payment_type = self.request.GET.get('payment_type', 'all')
        if current_payment_type and current_payment_type != 'all':
            payments_qs = payments_qs.filter(payment_type__iexact=current_payment_type)
        
        ctx['property_payments'] = payments_qs[:50]
        ctx['payment_active_month'] = active_month
        ctx['current_payment_type'] = current_payment_type

        # 5. Communication
        ctx['comm_logs'] = NotificationLog.objects.filter(tenant__property=prop).select_related('tenant').order_by('-created_at')[:50]
        ctx['announcement_form'] = AnnouncementForm()

        # Forms
        ctx['unit_form'] = UnitForm(property_obj=prop)
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
    model = Property
    template_name = "properties/partials/payments_table.html"
    context_object_name = "property_obj"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        prop = self.object
        
        month_str = self.request.GET.get('month')
        p_type = self.request.GET.get('payment_type', 'all')
        
        # Base Query: Filter by property AND exclude allocation children (show only receipts)
        payments_qs = Payment.objects.filter(
            tenant__property=prop
        ).exclude(
            reference__startswith="Allocation from"
        ).select_related('tenant', 'invoice')
        
        if month_str:
            try:
                year, month = map(int, month_str.split('-'))
                payments_qs = payments_qs.filter(payment_date__year=year, payment_date__month=month)
            except ValueError: pass
            
        if p_type and p_type != 'all':
            payments_qs = payments_qs.filter(payment_type__iexact=p_type)
            
        ctx['property_payments'] = payments_qs.order_by('-payment_date')
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