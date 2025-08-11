from django.shortcuts import get_object_or_404
from django.shortcuts import redirect, render
from django.urls import reverse_lazy, reverse
from django.views.generic import DetailView, ListView, CreateView, UpdateView, DeleteView
from django.views.generic.base import TemplateResponseMixin
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from .models import Property, Unit
from .forms import PropertyForm, UnitForm,LeaseForm, CombinedTenantLeaseForm
import json
from django.http import HttpResponseRedirect, HttpResponseBadRequest,Http404, JsonResponse


# mixin to pick HTMX template
class HTMXTemplateResponseMixin(TemplateResponseMixin):
    def render_to_response(self, context, **resp_kw):
        if self.request.headers.get("HX-Request"):
            tpl = getattr(self, "template_name_hx", self.template_name)
            return self.response_class(self.request, tpl, context, **resp_kw)
        return super().render_to_response(context, **resp_kw)



class PropertyListView(ListView):
    model = Property
    template_name = "properties/property_list.html"
    context_object_name = "properties"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['create_form'] = PropertyForm()
        # ensure 'property' always exists in template context
        #context['property'] = None 
        # Create a dict of property.id → PropertyForm(instance=property)
        context['edit_forms'] = {
            prop.id: PropertyForm(instance=prop) for prop in context['properties']
        }
        return context
    
    
class PropertyCreateView(CreateView):
    model = Property
    form_class = PropertyForm
    template_name = "properties/partials/property_form.html"
    success_url = reverse_lazy("property-list")

    def form_valid(self, form):
        messages.success(self.request, "Property added.")
        return super().form_valid(form)

class PropertyUpdateView(UpdateView):
    model = Property
    form_class = PropertyForm
    template_name = "properties/partials/property_form.html"
    success_url = reverse_lazy("property-list")

    def form_valid(self, form):
        messages.success(self.request, "Property updated.")
        return super().form_valid(form)

class PropertyDeleteView(DeleteView):
    model = Property
    template_name = "properties/partials/property_confirm_delete.html"
    success_url = reverse_lazy("property-list")

    def delete(self, request, *args, **kwargs):
        prop = self.get_object()
        messages.success(request, f"{prop.name} deleted.")
        return super().delete(request, *args, **kwargs)
    
    
    
    
    

class PropertyDetailView(DetailView):
    model = Property
    template_name = 'properties/property_detail.html'
    context_object_name = 'property_obj'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        
        # Get units queryset with related tenant and lease
        units_qs = self.object.units.select_related('lease__tenant')
        ctx['units'] = units_qs
        
        # Precompute unit count for efficiency
        ctx['units_count'] = units_qs.count()

        ctx['unit_id']       = self.kwargs.get('unit_id')
        ctx['unit_form']     = UnitForm()
        ctx['property_obj']  = self.object  # already available via context_object_name
        
        ctx['combined_form'] = CombinedTenantLeaseForm(initial={
            'property': self.object.id
        })

        return ctx






class UnitListView(ListView):
    """
    List units for a property (non-HTMX). Paginate if you expect many units.
    """
    model = Unit
    template_name = 'properties/partials/unit_table.html'   # full table (used by property detail)
    context_object_name = 'units'
    paginate_by = 25

    def get_queryset(self):
        property_pk = self.kwargs.get('pk')
        qs = Unit.objects.filter(property_id=property_pk).select_related('lease__tenant')
        status = self.request.GET.get('status')
        if status == 'occupied':
            qs = qs.filter(is_occupied=True)
        elif status == 'vacant':
            qs = qs.filter(is_occupied=False)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['property'] = get_object_or_404(Property, pk=self.kwargs.get('pk'))
        return ctx

class UnitCreateView(CreateView):
    model = Unit
    form_class = UnitForm
    template_name = 'properties/partials/unit_form.html'  # modal partial
    # success_url computed after creation to redirect to property detail

    def dispatch(self, request, *args, **kwargs):
        # ensure property exists and is available in this view
        self.property = get_object_or_404(Property, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **ctx):
        ctx = super().get_context_data(**ctx)
        ctx['property'] = self.property
        return ctx

    def form_valid(self, form):
        form.instance.property = self.property
        unit = form.save()
        messages.success(self.request, f"Unit «{unit.unit_number}» added.")
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse('property_detail', kwargs={'pk': self.property.pk})


class UnitUpdateView(UpdateView):
    model = Unit
    form_class = UnitForm
    template_name = 'properties/partials/unit_form.html'
    pk_url_kwarg = 'unit_pk'

    def dispatch(self, request, *args, **kwargs):
        # load parent property and the unit
        self.property = get_object_or_404(Property, pk=kwargs['pk'])
        self.object = get_object_or_404(Unit, pk=kwargs['unit_pk'], property=self.property)
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        # we've already resolved self.object in dispatch
        return self.object

    def get_context_data(self, **ctx):
        ctx = super().get_context_data(**ctx)
        ctx['property'] = self.property
        return ctx

    def form_valid(self, form):
        unit = form.save()
        messages.success(self.request, f"Unit «{unit.unit_number}» updated.")
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse('property_detail', kwargs={'pk': self.property.pk})


class UnitDeleteView(DeleteView):
    model = Unit
    template_name = 'properties/partials/unit_confirm_delete.html'
    pk_url_kwarg = 'unit_pk'

    def dispatch(self, request, *args, **kwargs):
        self.property = get_object_or_404(Property, pk=kwargs['pk'])
        self.object = get_object_or_404(Unit, pk=kwargs['unit_pk'], property=self.property)
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        return self.object

    def get_context_data(self, **ctx):
        ctx = super().get_context_data(**ctx)
        ctx['property'] = self.property
        return ctx

    def get_success_url(self):
        return reverse('property_detail', kwargs={'pk': self.property.pk})

    def delete(self, request, *args, **kwargs):
        unit = self.get_object()
        unit_id = unit.id
        unit_number = unit.unit_number
        unit.delete()
        messages.success(request, f"Unit «{unit_number}» deleted.")

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'message': f"Unit «{unit_number}» deleted.",
                'unit_id': unit_id,
            })

        return redirect(self.get_success_url())
