from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy            
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.http import JsonResponse
from django.contrib import messages
from django.db import transaction, IntegrityError
from apps.tenant_management.models import Unit, Property
from apps.tenant_management.forms import UnitForm
from django.db.models import Q
from decimal import Decimal
from django.template.loader import render_to_string
from django.http import HttpResponseRedirect
import logging
from django.http import Http404 
from apps.tenant_management.utils import filter_units_for_property

from django.core.exceptions import ObjectDoesNotExist

logger = logging.getLogger(__name__)

# apps/tenant_management/views.py
class UnitListView(ListView):
    model = Unit
    template_name = 'units/partials/unit_table.html'
    context_object_name = 'units'

    def get_queryset(self):
        property_pk = self.kwargs.get('pk')
        property_obj = get_object_or_404(Property, pk=property_pk)
        status = self.request.GET.get('status')
        if status == 'all':
            status = None
        return filter_units_for_property(property_obj, status=status)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        prop = get_object_or_404(Property, pk=self.kwargs.get('pk'))
        ctx['property'] = prop
        ctx['property_obj'] = prop           
        ctx['current_status'] = self.request.GET.get('status', 'all')
        return ctx






class UnitCreateView(CreateView):
    model = Unit
    form_class = UnitForm
    template_name = 'units/unit_form.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["property"] = get_object_or_404(Property, pk=self.kwargs["pk"])
        return ctx

    def form_valid(self, form):
        property_obj = get_object_or_404(Property, pk=self.kwargs["pk"])
        unit = form.save(commit=False)
        unit.property = property_obj

        try:
            with transaction.atomic():
                unit.save()
        except IntegrityError:
            form.add_error("unit_number", "Unit number already exists for this property.")
            return self.form_invalid(form)

        # AJAX response
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            html = render_to_string(
                "units/partials/unit_row.html",
                {"unit": unit, "property_obj": property_obj},
                request=self.request,
            )
            return JsonResponse({
                "success": True,
                "html": html,
                "row_id": f"unit-row-{unit.id}",
                "message": f'Unit "{unit.unit_number}" created successfully'
            })

        # Non-AJAX fallback
        messages.success(self.request, f'Unit "{unit.unit_number}" created successfully!')
        return HttpResponseRedirect(reverse_lazy("property_detail", kwargs={"pk": property_obj.pk}))

    def form_invalid(self, form):
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            html = render_to_string(
                self.template_name,
                {**self.get_context_data(), "form": form},
                request=self.request,
            )
            return JsonResponse({"success": False, "html": html}, status=400)

        return super().form_invalid(form)
    
    
    

class UnitUpdateView(UpdateView):
    model = Unit
    form_class = UnitForm
    template_name = "units/unit_form.html"
    context_object_name = "unit"

    def get_success_url(self):
        return reverse_lazy("property_detail", kwargs={"pk": self.object.property.pk})

    def get_queryset(self):
        property_id = self.kwargs.get("pk")        # property id from URL
        unit_id = self.kwargs.get("unit_pk")       # unit id from URL

        qs = Unit.objects.all()
        if property_id:
            qs = qs.filter(property_id=property_id)
        if unit_id:
            qs = qs.filter(pk=unit_id)

        # Debug logging to help trace what's being requested
        try:
            count = qs.count()
        except Exception:
            count = "<count error>"
        logger.debug("UnitUpdateView.get_queryset property_id=%s unit_id=%s qs_count=%s",
                     property_id, unit_id, count)
        return qs

    def get_object(self, queryset=None):
        queryset = queryset or self.get_queryset()
        unit_id = self.kwargs.get("unit_pk")
        if not unit_id:
            raise Http404("No unit_pk provided in URL")
        return get_object_or_404(queryset, pk=unit_id)


    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["property"] = self.object.property
        return context

    def form_valid(self, form):
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            try:
                self.object = form.save()
                html = render_to_string(
                    "units/partials/unit_row.html",
                    {"unit": self.object, "property_obj": self.object.property},
                    request=self.request,
                )
                return JsonResponse({
                    "success": True,
                    "html": html,
                    "row_id": f"unit-row-{self.object.id}",  # Fixed to match template ID
                    "message": f"Unit {self.object.unit_number} updated successfully"
                })
            except IntegrityError:
                form.add_error("unit_number", "Unit number already exists.")
                return self.form_invalid(form)
        
        # Non-AJAX fallback
        messages.success(self.request, f'Unit "{self.object.unit_number}" updated successfully.')
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




class UnitDeleteView(DeleteView):
    model = Unit
    template_name = "units/unit_confirm_delete.html"
    context_object_name = "unit"

    def get_queryset(self):
        property_id = self.kwargs.get("pk")
        unit_id = self.kwargs.get("unit_pk")

        qs = Unit.objects.all()
        if property_id:
            qs = qs.filter(property_id=property_id)
        if unit_id:
            qs = qs.filter(pk=unit_id)

        try:
            count = qs.count()
        except Exception:
            count = "<count error>"

        logger.debug(
            "UnitDeleteView.get_queryset property_id=%s unit_id=%s qs_count=%s",
            property_id,
            unit_id,
            count,
        )
        return qs

    def get_object(self, queryset=None):
        queryset = queryset or self.get_queryset()
        unit_id = self.kwargs.get("unit_pk")
        if not unit_id:
            raise Http404("No unit_pk provided in URL")
        return get_object_or_404(queryset, pk=unit_id)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["property"] = self.object.property
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
        row_id = f"unit-row-{self.object.id}"
        unit_number = self.object.unit_number

        # Prevent deletion if unit has an active lease
        try:
            lease = self.object.lease  # OneToOneField reverse accessor
        except ObjectDoesNotExist:
            lease = None

        if lease and lease.is_active:
            error_msg = f"Cannot delete unit {unit_number} with active leases."
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return JsonResponse({"success": False, "error": error_msg}, status=400)
            messages.error(request, error_msg)
            return HttpResponseRedirect(self.get_success_url())

        # Attempt deletion
        try:
            with transaction.atomic():
                self.object.delete()
        except Exception as e:
            error_msg = f"Delete failed: {str(e)}"
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return JsonResponse({"success": False, "error": error_msg}, status=500)
            messages.error(request, error_msg)
            return HttpResponseRedirect(self.get_success_url())

        # Success response
        success_msg = f"Unit {unit_number} deleted successfully"
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": True, "row_id": row_id, "message": success_msg})

        messages.success(request, success_msg)
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse_lazy("property_detail", kwargs={"pk": self.object.property.pk})
    
    def _render_messages(self):
        return render_to_string("messages/messages.html", {}, request=self.request)