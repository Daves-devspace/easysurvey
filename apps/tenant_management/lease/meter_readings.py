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
from apps.tenant_management.utils import filter_meter_readings_for_property
from apps.tenant_management.services.invoice_service import InvoiceService 

logger = logging.getLogger(__name__)

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
    model = MeterReading
    template_name = "meter_readings/form_partial.html"

    def get_last_valid_reading(self):
        unit = get_object_or_404(Unit, pk=self.kwargs["unit_id"])
        return unit.meter_readings.exclude(current_reading__isnull=True).order_by("-reading_date").first()

    def get_form_class(self):
        last = self.get_last_valid_reading()
        return MeterReadingUpdateForm if last else MeterReadingCreateForm

    def get_initial(self):
        initial = super().get_initial()
        last = self.get_last_valid_reading()
        if last: initial["previous_reading"] = last.current_reading
        return initial

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        unit = get_object_or_404(Unit, pk=self.kwargs["unit_id"])
        ctx["unit"] = unit
        last = self.get_last_valid_reading()
        ctx["is_update"] = False 
        ctx["has_history"] = bool(last)
        if last: ctx["previous_reading_display"] = last.current_reading 
        return ctx

    @transaction.atomic
    def form_valid(self, form):
        unit = get_object_or_404(Unit, pk=self.kwargs["unit_id"])
        form.instance.unit = unit
        form.instance.reading_date = form.cleaned_data["billing_period"]
        
        last = self.get_last_valid_reading()
        
        # --- Set Previous Reading Field ---
        if last:
            form.instance.previous_reading = last.current_reading
        else:
            # Baseline: Prev = Current to make Usage 0
            form.instance.current_reading = form.instance.previous_reading

        # --- SAVE & RELY ON SIGNAL ---
        self.object = form.save()
        
        # --- REFRESH TO GET SIGNAL CALCULATION ---
        self.object.refresh_from_db() 

        # Trigger Billing
        active_lease = unit.leases.filter(is_active=True).first()
        if self.object.usage is not None and self.object.usage > 0 and active_lease:
             # Set flag to prevent signal double-queueing
             self.object._processed_in_view = True 
             InvoiceService.upsert_water_invoice_line_from_reading(self.object)

        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            # Safe Extraction of Rate
            rate_val = self.object.rate_per_cubic_meter if self.object.rate_per_cubic_meter else Decimal('0.00')
            
            item = {
                "unit": unit,
                "tenant": active_lease.tenant if active_lease else None,
                "reading": self.object,
                "previous_current": self.object.previous_reading,
                "usage": self.object.usage,
                "rate": rate_val,
                "amount": self.object.amount,
            }
            row_html = render_to_string("meter_readings/partials/reading_row.html", {"item": item}, request=self.request)
            return JsonResponse({"success": True, "row_html": row_html, "unit_id": unit.id})

        return redirect("property_detail", pk=unit.property_id)

    def form_invalid(self, form):
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            ctx = self.get_context_data(form=form)
            return JsonResponse({"success": False, "form_html": render_to_string(self.template_name, ctx, request=self.request)}, status=400)
        return super().form_invalid(form)

class MeterReadingUpdateView(UpdateView):
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
        
        # Save & Refresh from Signal Logic
        self.object = form.save()
        self.object.refresh_from_db()
        
        if self.object.usage > 0 and active_lease:
             self.object._processed_in_view = True
             InvoiceService.upsert_water_invoice_line_from_reading(self.object)

        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            item = {
                "unit": unit,
                "tenant": active_lease.tenant if active_lease else None,
                "reading": self.object,
                "previous_current": self.object.previous_reading,
                "usage": self.object.usage,
                "amount": self.object.amount,
                "rate": self.object.rate_per_cubic_meter,
            }
            row_html = render_to_string("meter_readings/partials/reading_row.html", {"item": item}, request=self.request)
            return JsonResponse({"success": True, "row_html": row_html, "unit_id": unit.id})
        return redirect("property_detail", pk=unit.property_id)
        
    def form_invalid(self, form):
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            ctx = self.get_context_data(form=form)
            return JsonResponse({"success": False, "form_html": render_to_string(self.template_name, ctx, request=self.request)}, status=400)
        return super().form_invalid(form)

class MeterReadingDeleteView(DeleteView):
    model = MeterReading
    template_name = "meter_readings/confirm_delete.html"
    def delete(self, request, *args, **kwargs):
        messages.warning(request, "Meter reading deleted.")
        return super().delete(request, *args, **kwargs)
    def get_success_url(self):
         return reverse_lazy("property_detail", kwargs={"pk": self.object.unit.property.id})