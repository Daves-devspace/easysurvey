from django.shortcuts         import get_object_or_404, redirect
from django.urls             import reverse
from django.views.generic    import CreateView
from django.contrib.messages import success

from apps.tenant_management.models import Unit, Lease
from apps.tenant_management.forms  import LeaseForm

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.views.generic import CreateView, ListView, DetailView, UpdateView, DeleteView
from django.views.generic.edit import FormView
from django.urls import reverse, reverse_lazy
from django.http import JsonResponse
from django.db.models import Prefetch, Q, Count
from django.db import transaction


from apps.tenant_management.forms import (
    TenantCreationForm,
    LeaseCreationForm,
    CombinedTenantLeaseForm
)
from .services import TenantLeaseService
from apps.tenant_management.models import Tenant, Lease, Unit, Property
from django.template.loader import render_to_string
from django.http import HttpResponseRedirect
import logging
from django.core.exceptions import ValidationError
from django.views import View
logger = logging.getLogger(__name__)

from decimal import Decimal
from django.db import IntegrityError



class TenantLeaseCreateView(FormView):
    """
    Handles combined Tenant + Lease creation via AJAX.
    - GET returns JSON with rendered form HTML for modal injection.
    - POST validates the form, creates Tenant + Lease (atomic), and
      redirects to property detail page on success.
    """

    form_class = CombinedTenantLeaseForm
    template_name = "tenants/partials/tenant_lease_form.html"

    def dispatch(self, request, *args, **kwargs):
        """
        Ensure property and unit exist, and that the unit belongs to the property.
        Store them for later use.
        """
        self.property = get_object_or_404(Property, pk=kwargs.get("property_id"))
        self.unit = get_object_or_404(Unit, pk=kwargs.get("unit_id"), property=self.property)
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        """
        Hidden authoritative fields.  
        These values are set on the form regardless of client POST.
        """
        return {"property": self.property.id, "unit": self.unit.id}

    # ---------- Internal helpers ----------

    def _render_fragment(self, form):
        """
        Render the form partial (used for GET and invalid POST).  
        Always include `combined_form`, `current_property`, and `current_unit`  
        to avoid template context collisions.
        """
        ctx = {
            "combined_form": form,
            "current_property": self.property,
            "current_unit": self.unit,
        }
        try:
            return render_to_string(self.template_name, ctx, request=self.request)
        except Exception:
            logger.exception("Failed to render tenant_lease_form.html")
            return "<div class='alert alert-danger'>Rendering error. Check logs.</div>"

    # ---------- HTTP methods ----------

    def get(self, request, *args, **kwargs):
        """
        Return the blank form as JSON for modal body.
        """
        form = self.form_class(initial=self.get_initial())
        html = self._render_fragment(form)
        return JsonResponse({"html": html})

    def post(self, request, *args, **kwargs):
        """
        Process form submission.  
        On success: Redirect to property detail page with success message.
        On failure: JSON with form fragment and validation errors.
        """
        # Check if this is an AJAX request
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        
        form = self.form_class(request.POST, initial=self.get_initial())

        if not form.is_valid():
            if is_ajax:
                html = self._render_fragment(form)
                return JsonResponse({"success": False, "html": html, "errors": form.errors}, status=400)
            else:
                # Fallback for non-AJAX requests
                return self.form_invalid(form)

        tenant_data = {
            "full_name": form.cleaned_data["full_name"],
            "phone_number": form.cleaned_data["phone_number"],
            "email": form.cleaned_data.get("email"),
            "national_id": form.cleaned_data.get("national_id"),
            "property_id": self.property.id, 
        }
        lease_data = {
            "unit_id": self.unit.id,  # authoritative
            "start_date": form.cleaned_data["start_date"],
            "deposit_amount": form.cleaned_data.get("deposit_amount", Decimal("0.00")),
        }

        try:
            with transaction.atomic():
                result = TenantLeaseService.create_tenant_with_lease(tenant_data, lease_data)
        except ValidationError as e:
            form.add_error(None, getattr(e, "message", str(e)))
            if is_ajax:
                html = self._render_fragment(form)
                return JsonResponse({"success": False, "html": html, "errors": form.errors}, status=400)
            else:
                return self.form_invalid(form)
        except Exception:
            logger.exception("Unexpected error creating tenant+lease")
            form.add_error(None, "An unexpected error occurred. Please try again.")
            if is_ajax:
                html = self._render_fragment(form)
                return JsonResponse({"success": False, "html": html, "errors": form.errors}, status=400)
            else:
                return self.form_invalid(form)

        # Success: add message and redirect
        success_message = result.get("message", f"Tenant and lease created successfully for Unit {self.unit.unit_number}.")
        messages.success(self.request, success_message)
        
        redirect_url = reverse('property_detail', kwargs={'pk': self.property.pk})
        
        if is_ajax:
            # For AJAX requests, return redirect instruction
            return JsonResponse({
                "success": True,
                "redirect": redirect_url,
                "message": success_message,
            })
        else:
            # For regular form submissions
            return redirect(redirect_url)


    
    
# Helper function to build lease row context
def _build_lease_row_context(lease):
    """Return a 'row' dict used by the lease_row partial."""
    row = {
        "lease_obj": lease,
        "tenant": lease.tenant,
        "unit": lease.unit,
        "rent_amount": lease.rent_amount,
        "balance": lease.balance,
        "current_meter": lease.current_meter,
        "lease_start": lease.start_date,
        "lease_end": lease.end_date,
    }
    return row


class LeaseCreateView(CreateView):
    """Create a new lease."""
    model = Lease
    form_class = LeaseCreationForm
    template_name = 'leases/lease_form.html'

    def get_form_kwargs(self):
        """Pass property_id to form for unit filtering."""
        kwargs = super().get_form_kwargs()
        tenant = get_object_or_404(Tenant, pk=self.kwargs["tenant_id"])
        kwargs['property_id'] = tenant.property.id if tenant.property else None
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["tenant"] = get_object_or_404(Tenant, pk=self.kwargs["tenant_id"])
        return ctx

    def form_valid(self, form):
        tenant = get_object_or_404(Tenant, pk=self.kwargs["tenant_id"])
        lease = form.save(commit=False)
        lease.tenant = tenant

        try:
            with transaction.atomic():
                lease.save()
                # Mark unit as occupied
                lease.unit.is_occupied = True
                lease.unit.save()
        except IntegrityError:
            form.add_error(None, "Failed to create lease.")
            return self.form_invalid(form)

        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            # Build row context and return HTML
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
                "message": f'Lease created successfully for {tenant.full_name}!'
            })
        else:
            messages.success(self.request, f'Lease created successfully for {tenant.full_name}!')
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
        return reverse_lazy("property_detail", kwargs={"pk": tenant.property.pk})




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










class LeaseUpdateView(UpdateView):
    """Edit an existing lease."""
    model = Lease
    form_class = LeaseCreationForm
    template_name = "leases/lease_form.html"
    context_object_name = "lease"

    def get_form_kwargs(self):
        """Pass property_id to form for unit filtering."""
        kwargs = super().get_form_kwargs()
        kwargs['property_id'] = self.object.tenant.property.id if self.object.tenant.property else None
        return kwargs

    def get_success_url(self):
        return reverse_lazy("property_detail", kwargs={"pk": self.object.tenant.property.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["tenant"] = self.object.tenant
        return context

    def form_valid(self, form):
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            try:
                old_unit = self.object.unit
                self.object = form.save()
                
                # Update unit occupancy if unit changed
                if old_unit != self.object.unit:
                    old_unit.is_occupied = False
                    old_unit.save()
                    self.object.unit.is_occupied = True
                    self.object.unit.save()
                
                # Build row context and return HTML
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
            except IntegrityError:
                form.add_error(None, "Failed to update lease.")
                return self.form_invalid(form)
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


class LeaseDeleteView(DeleteView):
    model = Lease
    template_name = "leases/lease_confirm_delete.html"
    context_object_name = "lease"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["tenant"] = self.object.tenant
        return ctx

    # AJAX GET returns rendered confirm HTML
    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            html = render_to_string(self.template_name, self.get_context_data(), request=request)
            return JsonResponse({"success": True, "html": html})
        return super().get(request, *args, **kwargs)

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        row_id = f"lease-row-{self.object.id}"

        # Build row html before deleting (so template rendering doesn't fail)
        row_context = _build_lease_row_context(self.object)
        row_html = render_to_string(
            "leases/partials/lease_row.html", 
            {"row": row_context, "property_obj": self.object.tenant.property}, 
            request=request
        )

        try:
            with transaction.atomic():
                # mark unit vacant first
                if self.object.unit:
                    self.object.unit.is_occupied = False
                    self.object.unit.save()
                self.object.delete()
        except Exception as e:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return JsonResponse({"success": False, "error": str(e)}, status=500)
            from django.contrib import messages
            messages.error(request, f"Delete failed: {e}")
            return HttpResponseRedirect(self.get_success_url())

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({
                "success": True, 
                "row_id": row_id,
                "html": row_html,  # Return the HTML for potential UI updates
                "message": "Lease deleted successfully"
            })
        return super().delete(request, *args, **kwargs)

    def get_success_url(self):
        return reverse_lazy("property_detail", kwargs={"pk": self.object.tenant.property.pk})