import logging
from decimal import Decimal
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.template.loader import render_to_string
from django.db import transaction
from django.http import JsonResponse

from apps.tenant_management.forms import MeterReadingCreateForm, MeterReadingUpdateForm
from apps.tenant_management.models import MeterReading, Unit, Property 
from apps.tenant_management.utils import get_applicable_rate_for_date
from apps.tenant_management.utils import filter_meter_readings_for_property
from apps.tenant_management.services.invoice_service import InvoiceService 

logger = logging.getLogger(__name__)

# ... (MeterReadingListView remains same) ...
class MeterReadingListView(ListView):
    model = MeterReading
    template_name = "meter_readings/partials/meter_readings_table.html"
    context_object_name = "meter_readings"

    def get_queryset(self):
        property_pk = self.kwargs.get("pk")
        property_obj = get_object_or_404(Property, pk=property_pk)
        month = self.request.GET.get("month")
        return filter_meter_readings_for_property(property_obj, month)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['property_obj'] = get_object_or_404(Property, pk=self.kwargs.get("pk"))
        return ctx

class MeterReadingCreateView(CreateView):
    """Sets baseline reading (Move-in reading). No billing effect."""
    model = MeterReading
    form_class = MeterReadingCreateForm
    template_name = "meter_readings/form_partial.html"

    def get_initial(self):
        unit = get_object_or_404(Unit, pk=self.kwargs["unit_id"])
        last = unit.meter_readings.order_by("-reading_date").first()
        initial = super().get_initial()
        initial["previous_reading"] = last.current_reading if last and last.current_reading else Decimal("0.00")
        return initial

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["unit"] = get_object_or_404(Unit, pk=self.kwargs["unit_id"])
        ctx["is_update"] = False
        return ctx

    @transaction.atomic
    def form_valid(self, form):
        unit = get_object_or_404(Unit, pk=self.kwargs["unit_id"])
        form.instance.unit = unit
        form.instance.reading_date = form.cleaned_data["billing_period"]
        self.object = form.save()

        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            # Render row for UI update
            active_lease = unit.leases.filter(is_active=True).last()
            item = {
                "unit": unit,
                "tenant": active_lease.tenant if active_lease else None,
                "reading": self.object,
                "previous_current": self.object.previous_reading,
            }
            row_html = render_to_string("meter_readings/partials/reading_row.html", {"item": item}, request=self.request)
            return JsonResponse({"success": True, "row_html": row_html, "unit_id": unit.id})

        return redirect("property_detail", pk=unit.property_id)

class MeterReadingUpdateView(UpdateView):
    """
    Updates reading. Calculates usage. 
    Triggers InvoiceService to add Water Charge to the Pending Invoice.
    """
    model = MeterReading
    form_class = MeterReadingUpdateForm
    template_name = "meter_readings/form_partial.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["unit"] = self.object.unit
        ctx["is_update"] = True
        ctx["has_active_lease"] = self.object.unit.leases.filter(is_active=True).exists()
        return ctx

    @transaction.atomic
    def form_valid(self, form):
        unit = self.object.unit
        active_lease = unit.leases.filter(is_active=True).first()
        
        current_reading = form.cleaned_data.get("current_reading")
        
        # Validation
        if current_reading is not None:
            if not active_lease:
                form.add_error("current_reading", "Cannot add reading without active lease.")
                return self.form_invalid(form)
            
            if Decimal(current_reading) < Decimal(self.object.previous_reading):
                form.add_error("current_reading", "Current reading cannot be less than previous.")
                return self.form_invalid(form)

            # Update object fields
            form.instance.usage = Decimal(current_reading) - Decimal(self.object.previous_reading)
            form.instance.reading_date = form.cleaned_data["billing_period"]
            
            # Calculate amount for local object (UI display only)
            # The InvoiceService does the authoritative calculation
            rate = get_applicable_rate_for_date(unit.property.water_company, form.instance.reading_date)
            if rate:
                form.instance.amount = form.instance.usage * rate.rate_per_cubic_meter

        self.object = form.save()

        # --- TRIGGER BILLING ---
        if current_reading is not None and active_lease:
            try:
                # Call Service to upsert invoice line
                # Note: We don't need to pass billing_month_date if using the reading date
                InvoiceService.upsert_water_invoice_line_from_reading(self.object)
            except Exception as e:
                logger.exception("Invoice upsert failed for reading %s", self.object.pk)
                messages.warning(self.request, "Reading saved, but bill update failed. Check logs.")

        # AJAX Response
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            item = {
                "unit": unit,
                "tenant": active_lease.tenant if active_lease else None,
                "reading": self.object,
                "previous_current": self.object.previous_reading,
                "usage": form.instance.usage,
                "amount": form.instance.amount,
            }
            row_html = render_to_string("meter_readings/partials/reading_row.html", {"item": item}, request=self.request)
            return JsonResponse({"success": True, "row_html": row_html, "unit_id": unit.id})

        return redirect("property_detail", pk=unit.property_id)

    def form_invalid(self, form):
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            ctx = self.get_context_data(form=form)
            return JsonResponse({
                "success": False,
                "form_html": render_to_string(self.template_name, ctx, request=self.request),
            }, status=400)
        return super().form_invalid(form)

class MeterReadingDeleteView(DeleteView):
    model = MeterReading
    template_name = "meter_readings/confirm_delete.html"

    def delete(self, request, *args, **kwargs):
        messages.warning(request, "Meter reading deleted. Invoice lines may need manual adjustment.")
        return super().delete(request, *args, **kwargs)
        
    def get_success_url(self):
         return reverse_lazy("meter_readings:list", kwargs={"pk": self.object.unit.property.id})