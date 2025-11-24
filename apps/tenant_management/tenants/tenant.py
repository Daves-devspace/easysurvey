from django.conf import settings
from collections import defaultdict
from django.urls import path, include
from django.views.generic import DetailView, CreateView, ListView, UpdateView, DeleteView
from django.db.models import Prefetch, Q, OuterRef, Subquery, Sum, Value, FloatField
from django.db.models.functions import Coalesce
from django.db.models import DecimalField
from decimal import Decimal
from django.shortcuts import render, get_object_or_404, redirect
from apps.tenant_management.models import Tenant, Lease, Payment, Invoice, Property, Unit, Deposit, MeterReading, InvoiceLine,LedgerEntry
from apps.tenant_management.forms import TenantCreationForm, CombinedTenantLeaseForm
from django.contrib import messages
from django.urls import reverse_lazy
from django.http import JsonResponse,HttpResponseRedirect
from django.db import transaction, IntegrityError
from django.template.loader import render_to_string
from django.core.exceptions import ValidationError
from apps.tenant_management.tenants.tenants_aggregates import get_tenant_financials
from django.db.models import F, ExpressionWrapper
from django.http import Http404
from django.views import View

# views.py

from django.shortcuts import render, get_object_or_404
from django.views.generic import DetailView, View
from django.db.models import Sum, FloatField, Q
from datetime import datetime
from django.utils.dateparse import parse_date
from apps.tenant_management.utils import (
    get_tenant_leases_data
)



def _build_tenant_row_context(tenant):
    """Return a 'row' dict used by the tenant_row partial."""
    # Try to get an active lease related to the tenant (adjust related name if needed)
    lease = None
    if hasattr(tenant, "leases"):
        lease = tenant.leases.filter(is_active=True).first()
    elif hasattr(tenant, "lease_set"):
        lease = tenant.lease_set.filter(is_active=True).first()

    row = {
        "tenant": tenant,
        "unit": getattr(lease, "unit", None) if lease else None,
        "rent_amount": getattr(lease, "rent_amount", None) if lease else None,
        "balance": getattr(lease, "balance", None) if lease else None,
        "current_meter": getattr(lease, "current_meter", None) if lease else None,
        "lease_start": getattr(lease, "lease_start", None) if lease else None,
        "lease_end": getattr(lease, "lease_end", None) if lease else None,
        "unleased": lease is None,
    }
    return row



class TenantCreateView(CreateView):
    model = Tenant
    form_class = TenantCreationForm
    template_name = 'tenants/tenant_form.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["property"] = get_object_or_404(Property, pk=self.kwargs["property_id"])
        return ctx

    def form_valid(self, form):
        property_obj = get_object_or_404(Property, pk=self.kwargs["property_id"])
        tenant = form.save(commit=False)
        tenant.property = property_obj

        try:
            with transaction.atomic():
                tenant.save()
        except IntegrityError:
            # Unique constraint violated (phone_number probably)
            form.add_error("phone_number", "This phone number already exists for this property.")
            return self.form_invalid(form)

        # AJAX response: return row partial + id so JS can insert/replace
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            row = _build_tenant_row_context(tenant)
            html = render_to_string(
                "tenants/partials/tenant_row.html",
                {"row": row, "property_obj": property_obj},
                request=self.request,
            )
            return JsonResponse({
                "success": True,
                "row_id": f"tenant-row-{tenant.id}",
                "html": html,
                "message": f'Tenant "{tenant.full_name}" created'
            })

        messages.success(self.request, f'Tenant "{tenant.full_name}" created successfully!')
        return super().form_valid(form)

    def form_invalid(self, form):
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            html = render_to_string(self.template_name, {**self.get_context_data(), "form": form}, request=self.request)
            return JsonResponse({"success": False, "html": html}, status=400)
        return super().form_invalid(form)

    def get_success_url(self):
        return reverse_lazy("property_detail", kwargs={"pk": self.kwargs["property_id"]})



class TenantUpdateView(UpdateView):
    model = Tenant
    form_class = TenantCreationForm
    template_name = "tenants/tenant_form.html"
    context_object_name = "tenant"

    def get_queryset(self):
        property_id = self.kwargs.get("property_id")
        if property_id:
            return Tenant.objects.filter(property_id=property_id)
        return Tenant.objects.all()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["property"] = self.object.property
        return ctx

    def get_form_kwargs(self):
        """Pass the property to the form for validation."""
        kwargs = super().get_form_kwargs()
        kwargs['property'] = self.object.property
        return kwargs

    def form_valid(self, form):
        # AJAX path: return JSON + partial for inline update
        if self.request.headers.get("X-Requested-With") == "XMLHttpRequest":
            try:
                with transaction.atomic():
                    self.object = form.save()
            except IntegrityError:
                # This should be rare since we're validating in the form
                form.add_error(None, "A record with these details already exists.")
                return self.form_invalid(form)

            # Build the partial row context and return it
            row = _build_tenant_row_context(self.object)
            html = render_to_string(
                "tenants/partials/tenant_row.html",
                {"row": row, "property_obj": self.object.property},
                request=self.request,
            )
            return JsonResponse({
                "success": True,
                "row_id": f"tenant-row-{self.object.id}",
                "html": html,
                "message": "Tenant updated successfully"
            })

        # Non-AJAX: standard POST behavior with DB-backed validation handling
        try:
            with transaction.atomic():
                self.object = form.save()
        except IntegrityError:
            form.add_error(None, "A record with these details already exists.")
            return self.form_invalid(form)

        messages.success(self.request, f'Tenant "{self.object.full_name}" updated.')
        return super().form_valid(form)

    def form_invalid(self, form):
        # For AJAX, return the rendered form fragment (so the modal can be updated)
        if self.request.headers.get("X-Requested-With") == "XMLHttpRequest":
            html = render_to_string(
                self.template_name,
                {**self.get_context_data(), "form": form},
                request=self.request,
            )
            return JsonResponse({"success": False, "html": html}, status=400)

        return super().form_invalid(form)

    def get_success_url(self):
        return reverse_lazy("property_detail", kwargs={"pk": self.object.property.pk})
    
    
    

class TenantDeleteView(DeleteView):
    model = Tenant
    template_name = "tenants/tenant_confirm_delete.html"
    context_object_name = "tenant"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if hasattr(self, "object") and self.object:
            context["property"] = self.object.property
        return context

    def delete(self, request, *args, **kwargs):
        try:
            self.object = self.get_object()
        except Http404:
            return JsonResponse({"success": False, "error": "Tenant not found."}, status=404)

        tenant = self.object
        row_id = f"tenant-row-{tenant.id}"

        # Prevent deletion if tenant has active leases
        if tenant.leases.filter(is_active=True).exists():
            return JsonResponse(
                {"success": False, "error": "Cannot delete tenant with active leases."},
                status=400,
            )

        property_id = tenant.property.id
        tenant.delete()

        return JsonResponse({
            "success": True,
            "row_id": row_id,
            "message": "Tenant deleted successfully",
            "property_id": property_id
        })

    # 🔑 Prevent Django from redirecting after delete
    def form_valid(self, form):
        return self.delete(self.request, *self.args, **self.kwargs)

    def get_success_url(self):
        # Only used if someone calls delete without AJAX
        return reverse_lazy("property_detail", kwargs={"pk": self.object.property.pk})
    


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
    """
    Comprehensive Dashboard for a single Tenant.
    Shows all leases (active/expired), financial summary, and payment history.
    """
    model = Tenant
    template_name = "tenants/tenant_detail.html"
    context_object_name = "tenant"
    pk_url_kwarg = "tenant_id"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        tenant = self.object

        # 1. Financial Data & Leases (Using Shared Utility)
        leases_data, aggregates = get_tenant_leases_data(tenant)
        ctx['leases_data'] = leases_data
        ctx.update(aggregates)

        # 2. Data for Tables
        ctx['invoices'] = Invoice.objects.filter(tenant=tenant).order_by("-billing_period_start")
        
        # --- FIX: Filter Payments to remove duplicate "Master" records ---
        # We exclude 'MIXED' type because those represent the "Total Container" 
        # for split payments. We only want to see the actual breakdown (RENT, DEPOSIT, CREDIT).
        ctx['payments'] = Payment.objects.filter(tenant=tenant)\
            .exclude(payment_type='MIXED')\
            .select_related("invoice")\
            .order_by("-payment_date")
            
        # Meter Readings
        ctx['meter_readings'] = MeterReading.objects.filter(
            unit__leases__tenant=tenant
        ).select_related("unit", "unit__property").order_by("-reading_date").distinct()

        # 3. Data for "Assign Unit" Modal
        properties_with_vacancies = Property.objects.prefetch_related(
            Prefetch("units", queryset=Unit.objects.filter(is_occupied=False).order_by("unit_number"))
        ).distinct()
        
        available_units_by_property = [
            {"property": p, "units": list(p.units.all())} 
            for p in properties_with_vacancies if p.units.exists()
        ]
        ctx['available_units_by_property'] = available_units_by_property

        return ctx




# HTMX endpoint for filtering invoices
from datetime import date, timedelta
class TenantInvoicesFilterView(View):
    def get(self, request, tenant_id):
        status = request.GET.get("status", "all")
        billing_period = request.GET.get("billing_period", "all")
        tenant = get_object_or_404(Tenant, id=tenant_id)

        # Base queryset for tenant invoices
        invoices = Invoice.objects.filter(
            lines__lease__tenant=tenant
        ).distinct().order_by("-billing_period_start")

        # Annotate total water usage and amount per invoice
        invoices = invoices.annotate(
            total_water_usage=Sum("lines__meter_reading__usage", output_field=FloatField()),
            total_water_amount=Sum("lines__amount", output_field=FloatField())
        )

        # Apply status filtering
        if status == "unpaid":
            invoices = invoices.filter(is_paid=False)
        elif status == "paid":
            invoices = invoices.filter(is_paid=True)

        # Apply billing period filtering
        if billing_period != "all":
            try:
                year, month = map(int, billing_period.split("-"))

                # Start and end of selected month
                month_start = date(year, month, 1)
                next_month = month + 1 if month < 12 else 1
                next_year = year if month < 12 else year + 1
                month_end = date(next_year, next_month, 1) - timedelta(days=1)

                # Invoices whose billing period overlaps that month
                invoices = invoices.filter(
                    billing_period_start__lte=month_end,
                    billing_period_end__gte=month_start,
                )
            except ValueError:
                pass


        return render(
            request,
            "tenants/partials/_invoice_rows.html",
            {
                "invoices": invoices,
                "tenant": tenant
            },
        )


# HTMX endpoint for filtering meter readings
class TenantMeterReadingsFilterView(View):
    def get(self, request, tenant_id):
        billing_period = request.GET.get("billing_period", "all")
        tenant = get_object_or_404(Tenant, id=tenant_id)

        # Base queryset for tenant meter readings
        meter_readings = MeterReading.objects.filter(
            unit__leases__tenant=tenant  # change from 'lease' to 'leases'
        ).select_related("unit", "unit__property").order_by("-reading_date")

        # Apply billing period filtering
        if billing_period != "all":
            try:
                year, month = map(int, billing_period.split("-"))
                meter_readings = meter_readings.filter(
                    reading_date__year=year,
                    reading_date__month=month
                )
            except ValueError:
                pass


        return render(
            request,
            "tenants/partials/_meter_readings_rows.html",
            {
                "meter_readings": meter_readings,
                "tenant": tenant
            },
        )