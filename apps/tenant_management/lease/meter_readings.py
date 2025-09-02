import logging
from decimal import Decimal
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from apps.tenant_management.forms import MeterReadingCreateForm, MeterReadingUpdateForm
from apps.tenant_management.models import MeterReading, Unit, Property
from apps.tenant_management.utils import filter_meter_readings_for_property 
from apps.tenant_management.billings.services import upsert_water_invoice_line_from_reading, get_applicable_rate_for_date
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from decimal import Decimal
from apps.tenant_management.billings.services import q  
from django.urls import reverse
from django.db import transaction
from django.http import JsonResponse

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
        prop = get_object_or_404(Property, pk=self.kwargs.get("pk"))
        
        ctx['property'] = prop
        ctx['property_obj'] = prop
        ctx['current_month'] = self.request.GET.get("month", "")
        return ctx






class MeterReadingCreateView(CreateView):
    model = MeterReading
    form_class = MeterReadingCreateForm
    template_name = "meter_readings/form_partial.html"

    def get_initial(self):
        initial = super().get_initial()
        unit = get_object_or_404(Unit, pk=self.kwargs["unit_id"])
        last = unit.meter_readings.order_by("-reading_date").first()
        initial["previous_reading"] = (
            last.current_reading if last and last.current_reading is not None else Decimal("0.00")
        )
        return initial

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # always inject
        ctx["unit"] = get_object_or_404(Unit, pk=self.kwargs["unit_id"])
        ctx["is_update"] = False
        return ctx

    @transaction.atomic
    def form_valid(self, form):
        unit = get_object_or_404(Unit, pk=self.kwargs["unit_id"])
        form.instance.unit = unit
        form.instance.reading_date = form.cleaned_data["billing_period"]
        self.object = form.save()

        messages.success(self.request, f"Baseline reading saved for unit {unit.unit_number}.")

        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            active_lease = (
                unit.leases.filter(is_active=True).select_related("tenant").order_by("-start_date").first()
            )
            tenant = active_lease.tenant if active_lease else None
            item = {
                "unit": unit,
                "tenant": tenant,
                "reading": self.object,
                "previous_current": self.object.previous_reading,
                "usage": None,
                "rate": None,
                "amount": None,
            }
            row_html = render_to_string(
                "meter_readings/partials/reading_row.html", {"item": item}, request=self.request
            )
            return JsonResponse({
                "success": True,
                "row_html": row_html,
                "unit_id": unit.id,
                "messages": self._render_messages(),
            })

        return redirect("property_detail", pk=unit.property_id)

    def form_invalid(self, form):
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            ctx = self.get_context_data(form=form)  # ✅ injects unit + is_update automatically
            return JsonResponse({
                "success": False,
                "form_html": render_to_string(self.template_name, ctx, request=self.request),
                "messages": self._render_messages(),
            })
        return super().form_invalid(form)

    def _render_messages(self):
        return render_to_string("messages/messages.html", {}, request=self.request)


class MeterReadingUpdateView(UpdateView):
    model = MeterReading
    form_class = MeterReadingUpdateForm
    template_name = "meter_readings/form_partial.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # always inject
        ctx["unit"] = self.object.unit
        ctx["is_update"] = True
        return ctx

    @transaction.atomic
    def form_valid(self, form):
        prev = self.object.previous_reading
        curr = Decimal(form.cleaned_data["current_reading"])

        if curr < Decimal(prev):
            form.add_error("current_reading", "Current reading cannot be less than previous.")
            return self.form_invalid(form)

        form.instance.usage = curr - Decimal(prev)
        form.instance.reading_date = form.cleaned_data["billing_period"]

        rate = get_applicable_rate_for_date(self.object.unit.property.water_company, form.instance.reading_date)
        form.instance.amount = (
            Decimal(form.instance.usage) * Decimal(
                getattr(rate, "rate_per_cubic_meter", getattr(rate, "rate_per_unit", 0))
            ) if rate else None
        )

        self.object = form.save()

        try:
            upsert_water_invoice_line_from_reading(self.object)
        except Exception:
            logger.exception("Invoice upsert failed for reading %s", self.object.pk)
            messages.error(self.request, "Reading saved but invoice update failed.")
        else:
            messages.success(self.request, f"Reading updated for unit {self.object.unit.unit_number}.")

        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            unit = self.object.unit
            active_lease = (
                unit.leases.filter(is_active=True).select_related("tenant").order_by("-start_date").first()
            )
            tenant = active_lease.tenant if active_lease else None
            item = {
                "unit": unit,
                "tenant": tenant,
                "reading": self.object,
                "previous_current": prev,
                "usage": form.instance.usage,
                "rate": getattr(rate, "rate_per_cubic_meter", None) if rate else None,
                "amount": form.instance.amount,
            }
            row_html = render_to_string(
                "meter_readings/partials/reading_row.html", {"item": item}, request=self.request
            )
            return JsonResponse({
                "success": True,
                "row_html": row_html,
                "unit_id": unit.id,
                "messages": self._render_messages(),
            })

        return redirect("property_detail", pk=self.object.unit.property_id)

    def form_invalid(self, form):
        if self.request.headers.get("x-requested-with") == "XMLHttpRequest":
            ctx = self.get_context_data(form=form)  # ✅ injects unit + is_update automatically
            return JsonResponse({
                "success": False,
                "form_html": render_to_string(self.template_name, ctx, request=self.request),
                "messages": self._render_messages(),
            })
        return super().form_invalid(form)

    def _render_messages(self):
        return render_to_string("messages/messages.html", {}, request=self.request)





class MeterReadingDeleteView(DeleteView):
    """Allow deleting a meter reading (admin only)."""
    model = MeterReading
    template_name = "meter_readings/confirm_delete.html"

    def get_success_url(self):
        return reverse_lazy("meter_readings:list", kwargs={"unit_id": self.object.unit.id})

    def delete(self, request, *args, **kwargs):
        messages.warning(request, "Meter reading deleted. Invoice lines may need manual adjustment.")
        return super().delete(request, *args, **kwargs)
