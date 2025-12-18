import logging
import datetime
from calendar import monthrange
from decimal import Decimal
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.template.loader import render_to_string
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone

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

    def get_target_date_context(self):
        month_str = self.request.GET.get("month")
        if month_str:
            try:
                year, month = map(int, month_str.split("-"))
                return datetime.date(year, month, 1)
            except ValueError:
                pass
        return timezone.now().date().replace(day=1)

    def get_previous_reading_obj(self):
        """
        Time-Travel Logic: Finds the reading used as the baseline for this bill.
        FIX: Look before the START OF NEXT MONTH to catch mid-month baselines.
        """
        unit = get_object_or_404(Unit, pk=self.kwargs["unit_id"])
        
        # Current Target Month Start (e.g., Dec 1)
        target_start = self.get_target_date_context()
        
        # Calculate Start of NEXT Month (e.g., Jan 1)
        # We want the latest reading that happened BEFORE Jan 1 (so Dec 15 is included)
        if target_start.month == 12:
            next_month_start = target_start.replace(year=target_start.year + 1, month=1)
        else:
            next_month_start = target_start.replace(month=target_start.month + 1)
        
        return unit.meter_readings.filter(
            reading_date__lt=next_month_start
        ).exclude(
            current_reading__isnull=True
        ).order_by("-reading_date", "-id").first()

    def get_form_class(self):
        prev = self.get_previous_reading_obj()
        return MeterReadingUpdateForm if prev else MeterReadingCreateForm

    def get_initial(self):
        initial = super().get_initial()
        initial["billing_period"] = self.get_target_date_context()
        prev = self.get_previous_reading_obj()
        if prev: 
            initial["previous_reading"] = prev.current_reading
        return initial

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        unit = get_object_or_404(Unit, pk=self.kwargs["unit_id"])
        ctx["unit"] = unit
        prev = self.get_previous_reading_obj()
        ctx["is_update"] = False 
        ctx["has_history"] = bool(prev)
        if prev: 
            ctx["previous_reading_display"] = prev.current_reading 
            ctx["previous_reading_date"] = prev.reading_date
        return ctx

    @transaction.atomic
    def form_valid(self, form):
        unit = get_object_or_404(Unit, pk=self.kwargs["unit_id"])
        form.instance.unit = unit
        
        # Date Logic
        billing_period = form.cleaned_data["billing_period"]
        last_day = monthrange(billing_period.year, billing_period.month)[1]
        
        # If current month, use today; else use end of month
        today = timezone.now().date()
        if billing_period.year == today.year and billing_period.month == today.month:
             form.instance.reading_date = today
        else:
             form.instance.reading_date = billing_period.replace(day=last_day)
        
        # 2. Re-fetch Previous Reading using the ROBUST logic (Start of Next Month)
        period_start = billing_period.replace(day=1)
        if period_start.month == 12:
            next_month_start = period_start.replace(year=period_start.year + 1, month=1)
        else:
            next_month_start = period_start.replace(month=period_start.month + 1)

        prev_obj = unit.meter_readings.filter(
            reading_date__lt=next_month_start
        ).exclude(current_reading__isnull=True).order_by("-reading_date", "-id").first()
        
        if prev_obj:
            # SCENARIO B: Bill
            form.instance.previous_reading = prev_obj.current_reading
            form.instance.previous_reading_date = prev_obj.reading_date
            
            curr = form.cleaned_data.get("current_reading")
            prev_val = prev_obj.current_reading
            
            if curr is not None and curr < prev_val:
                form.add_error("current_reading", f"Current ({curr}) cannot be less than Previous ({prev_val}).")
                return self.form_invalid(form)
            form.instance.usage = curr - prev_val
        else:
            # SCENARIO A: Baseline
            if not form.instance.current_reading:
                 form.instance.current_reading = form.instance.previous_reading
            form.instance.previous_reading_date = form.instance.reading_date
            form.instance.usage = Decimal('0.00')
            form.instance.amount = Decimal('0.00')

        self.object = form.save()
        self.object.refresh_from_db() 

        active_lease = unit.leases.filter(is_active=True).first()
        if self.object.usage is not None and self.object.usage > 0 and active_lease:
             self.object._processed_in_view = True 
             InvoiceService.upsert_water_invoice_line_from_reading(self.object)

        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
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
        
        # --- Date Logic ---
        billing_period = form.cleaned_data["billing_period"]
        last_day = monthrange(billing_period.year, billing_period.month)[1]
        today = timezone.now().date()
        if billing_period.year == today.year and billing_period.month == today.month:
             form.instance.reading_date = today
        else:
             form.instance.reading_date = billing_period.replace(day=last_day)
        
        current_reading = form.cleaned_data.get("current_reading")
        
        if current_reading is not None:
            if not active_lease:
                form.add_error("current_reading", "Cannot add reading without active lease.")
                return self.form_invalid(form)
                
            prev = self.object.previous_reading
            if Decimal(current_reading) < Decimal(prev):
                form.add_error("current_reading", f"Current reading cannot be less than previous ({prev}).")
                return self.form_invalid(form)
            
            form.instance.usage = Decimal(current_reading) - Decimal(prev)
            
        self.object = form.save()
        self.object.refresh_from_db()
        
        if self.object.usage is not None and self.object.usage > 0 and active_lease:
             self.object._processed_in_view = True
             InvoiceService.upsert_water_invoice_line_from_reading(self.object)

        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            rate_val = self.object.rate_per_cubic_meter if self.object.rate_per_cubic_meter else Decimal('0.00')
            item = {
                "unit": unit,
                "tenant": active_lease.tenant if active_lease else None,
                "reading": self.object,
                "previous_current": self.object.previous_reading,
                "usage": self.object.usage,
                "amount": self.object.amount,
                "rate": rate_val,
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