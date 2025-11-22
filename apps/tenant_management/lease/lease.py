import logging
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.views.generic import CreateView, ListView, DetailView, UpdateView, DeleteView
from django.views.generic.edit import FormView
from django.urls import reverse, reverse_lazy
from django.http import JsonResponse, HttpResponseRedirect
from django.db import transaction, IntegrityError
from django.forms import HiddenInput
from django.template.loader import render_to_string
from django.core.exceptions import ValidationError

from apps.tenant_management.models import Tenant, Lease, Unit, Property
from apps.tenant_management.forms import (
    LeaseCreationForm,
    CombinedTenantLeaseForm
)
from .services import TenantLeaseService
from apps.tenant_management.services.invoice_service import InvoiceService

logger = logging.getLogger(__name__)


# --- Helper to build lease row context for AJAX ---
def _build_lease_row_context(lease):
    """Return a 'row' dict used by the lease_row partial."""
    return {
        "lease_obj": lease,
        "tenant": lease.tenant,
        "unit": lease.unit,
        "rent_amount": lease.unit.rent_amount,
        "lease_start": lease.start_date,
        "deposit": lease.deposit_amount
    }

# ==============================================================================
# 1. Combined Tenant + Lease Creation (The "Add Tenant" Button)
# ==============================================================================

class TenantLeaseCreateView(FormView):
    """
    Handles combined Tenant + Lease creation via AJAX.
    Delegates logic to TenantLeaseService.
    """
    form_class = CombinedTenantLeaseForm
    template_name = "tenants/partials/tenant_lease_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.property = get_object_or_404(Property, pk=kwargs.get("property_id"))
        self.unit = get_object_or_404(Unit, pk=kwargs.get("unit_id"), property=self.property)
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        return {"property": self.property.id, "unit": self.unit.id}

    def _render_fragment(self, form):
        ctx = {
            "combined_form": form,
            "current_property": self.property,
            "current_unit": self.unit,
        }
        return render_to_string(self.template_name, ctx, request=self.request)

    def get(self, request, *args, **kwargs):
        form = self.form_class(initial=self.get_initial())
        return JsonResponse({"html": self._render_fragment(form)})

    def post(self, request, *args, **kwargs):
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        form = self.form_class(request.POST, initial=self.get_initial())

        if not form.is_valid():
            if is_ajax:
                return JsonResponse({"success": False, "html": self._render_fragment(form), "errors": form.errors}, status=400)
            return self.form_invalid(form)

        # Prepare Data
        tenant_data = {
            "full_name": form.cleaned_data["full_name"],
            "phone_number": form.cleaned_data["phone_number"],
            "email": form.cleaned_data.get("email"),
            "national_id": form.cleaned_data.get("national_id"),
            "property_id": self.property.id, 
        }
        lease_data = {
            "unit_id": self.unit.id,
            "start_date": form.cleaned_data["start_date"],
            "deposit_amount": form.cleaned_data.get("deposit_amount", Decimal("0.00")),
            # --- NEW: Pass the initial reading ---
            "initial_reading": form.cleaned_data.get("initial_reading"),
        }

        try:
            # Call Service (Handles creation + Invoice generation + Baseline Reading)
            result = TenantLeaseService.save_tenant_with_lease(tenant_data, lease_data)
        except ValidationError as e:
            form.add_error(None, getattr(e, "message", str(e)))
            if is_ajax:
                return JsonResponse({"success": False, "html": self._render_fragment(form), "errors": form.errors}, status=400)
            return self.form_invalid(form)
        except Exception:
            logger.exception("Unexpected error creating tenant+lease")
            form.add_error(None, "An unexpected error occurred.")
            if is_ajax:
                return JsonResponse({"success": False, "html": self._render_fragment(form), "errors": form.errors}, status=400)
            return self.form_invalid(form)

        success_message = result.get("message")
        messages.success(self.request, success_message)
        
        redirect_url = reverse('property_detail', kwargs={'pk': self.property.pk})
        
        if is_ajax:
            return JsonResponse({"success": True, "redirect": redirect_url, "message": success_message})
        return redirect(redirect_url)


# ==============================================================================
# 2. Standalone Lease Creation (Existing Tenant)
# ==============================================================================

class LeaseCreateView(CreateView):
    """
    Create a new lease for an EXISTING tenant.
    Must manually trigger InvoiceService here as it doesn't use TenantLeaseService.
    """
    model = Lease
    form_class = LeaseCreationForm
    template_name = 'leases/lease_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        tenant = get_object_or_404(Tenant, pk=self.kwargs["tenant_id"])
        kwargs['property_id'] = tenant.property.id if tenant.property else None
        return kwargs

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        unit_id = self.kwargs.get('unit_id')
        if unit_id:
            unit = get_object_or_404(Unit, pk=unit_id)
            form.fields['unit'].queryset = Unit.objects.filter(pk=unit.pk)
            form.initial['unit'] = unit.pk
            form.fields['unit'].widget = HiddenInput()
        return form

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["tenant"] = get_object_or_404(Tenant, pk=self.kwargs["tenant_id"])
        return ctx

    def form_valid(self, form):
        tenant = get_object_or_404(Tenant, pk=self.kwargs["tenant_id"])
        unit_id = self.kwargs.get('unit_id') or form.cleaned_data['unit'].id

        try:
            with transaction.atomic():
                # Lock Unit
                unit_obj = Unit.objects.select_for_update().get(pk=unit_id)
                
                # Validations
                if tenant.property_id != unit_obj.property_id:
                    form.add_error(None, "Unit does not belong to tenant's property.")
                    return self.form_invalid(form)
                if unit_obj.is_occupied:
                    form.add_error(None, "Unit is already occupied.")
                    return self.form_invalid(form)

                # Save Lease
                lease = form.save(commit=False)
                lease.tenant = tenant
                lease.unit = unit_obj
                lease.save()

                # Mark Occupied
                unit_obj.is_occupied = True
                unit_obj.save(update_fields=['is_occupied'])

                # --- TRIGGER BILLING ---
                # Generate the Move-in Invoice (Rent + Deposit)
                InvoiceService.upsert_rent_invoice_line_for_lease(lease, billing_date=lease.start_date)

        except IntegrityError:
            form.add_error(None, "Database error. Please try again.")
            return self.form_invalid(form)

        # Response Handling
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            row = _build_lease_row_context(lease)
            html = render_to_string(
                "leases/partials/lease_row.html",
                {"row": row, "property_obj": tenant.property},
                request=self.request,
            )
            return JsonResponse({
                "success": True,
                "row_id": f"lease-row-{lease.id}",
                "html": html,
                "message": f'Lease created for {tenant.full_name}. Invoice generated.'
            })

        messages.success(self.request, f'Lease created for {tenant.full_name}. Invoice generated.')
        return super().form_valid(form)

    def form_invalid(self, form):
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            html = render_to_string(
                self.template_name,
                {**self.get_context_data(), "form": form},
                request=self.request,
            )
            return JsonResponse({"success": False, "html": html}, status=400)
        return super().form_invalid(form)

    def get_success_url(self):
        tenant = get_object_or_404(Tenant, pk=self.kwargs["tenant_id"])
        return reverse_lazy("tenant_detail", kwargs={"pk": tenant.property.pk})


# ==============================================================================
# 3. Lease Management (Update, Delete, List, Details)
# ==============================================================================

class LeaseUpdateView(UpdateView):
    model = Lease
    form_class = LeaseCreationForm
    template_name = "leases/lease_form.html"
    context_object_name = "lease"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['property_id'] = self.object.tenant.property.id if self.object.tenant.property else None
        return kwargs

    def form_valid(self, form):
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            try:
                new_unit = form.cleaned_data.get('unit')
                old_unit = self.object.unit

                with transaction.atomic():
                    # Manage Unit Swapping if unit changed
                    if new_unit and old_unit.pk != new_unit.pk:
                        # Lock both
                        Unit.objects.select_for_update().filter(pk__in=[old_unit.pk, new_unit.pk])
                        
                        old_unit.is_occupied = False
                        old_unit.save()
                        new_unit.is_occupied = True
                        new_unit.save()
                    
                    self.object = form.save()

                row = _build_lease_row_context(self.object)
                html = render_to_string(
                    "leases/partials/lease_row.html",
                    {"row": row, "property_obj": self.object.tenant.property},
                    request=self.request,
                )
                return JsonResponse({
                    "success": True,
                    "row_id": f"lease-row-{self.object.id}",
                    "html": html,
                    "message": "Lease updated successfully"
                })
            except Exception as e:
                form.add_error(None, f"Update failed: {str(e)}")
                return self.form_invalid(form)
        return super().form_valid(form)

class LeaseDeleteView(DeleteView):
    model = Lease
    template_name = "leases/lease_confirm_delete.html"
    
    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        try:
            with transaction.atomic():
                TenantLeaseService.end_lease_and_free_unit(self.object.id)
                # Actually delete the object after freeing unit
                self.object.delete()
                
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return JsonResponse({"success": True, "message": "Lease deleted and unit freed."})
                
        except Exception as e:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return JsonResponse({"success": False, "error": str(e)}, status=500)
            messages.error(request, f"Delete failed: {e}")
            
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse_lazy("property_detail", kwargs={"pk": self.object.tenant.property.pk})

# ... (LeaseListView, LeaseDetailView, get_units_by_property remain standard) ...
class LeaseListView(ListView):
    model = Lease
    template_name = 'leases/lease_list.html'
    context_object_name = 'leases'
    paginate_by = 20

    def get_queryset(self):
        return Lease.objects.select_related('tenant', 'unit__property').order_by('-start_date')

class LeaseDetailView(DetailView):
    model = Lease
    template_name = 'leases/lease_detail.html'
    context_object_name = 'lease'

def get_units_by_property(request):
    """API for dynamic dropdowns"""
    property_id = request.GET.get('property_id')
    units = TenantLeaseService.get_available_units(property_id) if property_id else []
    data = [{'id': u.id, 'text': f"{u.unit_number} - Ksh {u.rent_amount}"} for u in units]
    return JsonResponse({'units': data})