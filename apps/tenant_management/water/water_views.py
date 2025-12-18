from django.contrib import messages
from django.views.generic import CreateView, UpdateView, DeleteView
from apps.tenant_management.models import WaterCompany, WaterRate
from apps.tenant_management.forms import WaterCompanyForm, WaterRateForm
from django.urls import reverse_lazy


# --- Helper Mixin ---
class RedirectToRefererMixin:
    """Redirects back to the page that initiated the request."""
    def get_success_url(self):
        return self.request.META.get('HTTP_REFERER') or reverse_lazy('property-list')
# --- Water Company Views ---
class WaterCompanyCreateView(RedirectToRefererMixin, CreateView):
    model = WaterCompany
    form_class = WaterCompanyForm
    template_name = "properties/partials/water_company_form.html"

    def form_valid(self, form):
        messages.success(self.request, "Water Company added successfully.")
        return super().form_valid(form)

class WaterCompanyUpdateView(RedirectToRefererMixin, UpdateView):
    model = WaterCompany
    form_class = WaterCompanyForm
    template_name = "properties/partials/water_company_form.html"

    def form_valid(self, form):
        messages.success(self.request, "Water Company updated.")
        return super().form_valid(form)

class WaterCompanyDeleteView(RedirectToRefererMixin, DeleteView):
    model = WaterCompany
    template_name = "properties/partials/property_confirm_delete.html" # Reusing generic delete template

    def delete(self, request, *args, **kwargs):
        messages.success(request, "Water Company deleted.")
        return super().delete(request, *args, **kwargs)

# --- Water Rate Views ---
class WaterRateCreateView(RedirectToRefererMixin, CreateView):
    model = WaterRate
    form_class = WaterRateForm
    template_name = "properties/partials/water_rate_form.html"

    def form_valid(self, form):
        messages.success(self.request, "Water Rate set successfully.")
        return super().form_valid(form)

class WaterRateUpdateView(RedirectToRefererMixin, UpdateView):
    model = WaterRate
    form_class = WaterRateForm
    template_name = "properties/partials/water_rate_form.html"

    def form_valid(self, form):
        messages.success(self.request, "Water Rate updated.")
        return super().form_valid(form)

class WaterRateDeleteView(RedirectToRefererMixin, DeleteView):
    model = WaterRate
    template_name = "properties/partials/property_confirm_delete.html" # Reusing generic delete template

    def delete(self, request, *args, **kwargs):
        messages.success(request, "Water Rate deleted.")
        return super().delete(request, *args, **kwargs)