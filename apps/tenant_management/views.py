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
from django.http import HttpResponseRedirect

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
        ctx['units']         = self.object.units.select_related('lease__tenant')
        ctx['unit_id']       = self.kwargs.get('unit_id')
        ctx['unit_form']     = UnitForm()
        # You can still create a combined_form if you need default property:
        ctx['combined_form'] = CombinedTenantLeaseForm(initial={
            'property': self.object.id
        })
        return ctx



class UnitListView(HTMXTemplateResponseMixin, ListView):
    model = Unit
    context_object_name = 'units'
    template_name = 'properties/partials/unit_table.html'
    template_name_hx = 'properties/partials/unit_table.html'
    paginate_by = 25

    def get_queryset(self):
        property_id = self.kwargs['pk']
        qs = Unit.objects.filter(property_id=property_id).select_related('lease__tenant')

        status = self.request.GET.get('status')
        if status == 'occupied':
            qs = qs.filter(is_occupied=True)
        elif status == 'vacant':
            qs = qs.filter(is_occupied=False)
        return qs




class UnitCreateView(SuccessMessageMixin, CreateView):
    model            = Unit
    form_class       = UnitForm
    template_name    = 'properties/partials/unit_form.html'
    success_message  = "Unit «%(unit_number)s» was added successfully."

    def dispatch(self, request, *args, **kwargs):
        # grab the Property once, store on self
        self.property = get_object_or_404(Property, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        # associate the unit with its property, then let the mixin handle saving+message+redirect
        form.instance.property = self.property
        return super().form_valid(form)

    def get_success_url(self):
        # go back to the property detail page
        return reverse('property_detail', kwargs={'pk': self.property.pk})




class UnitUpdateView(HTMXTemplateResponseMixin, UpdateView):
    model = Unit
    form_class = UnitForm
    pk_url_kwarg = 'unit_pk'
    template_name = 'properties/partials/unit_form.html'
    template_name_hx = 'properties/partials/unit_form.html'

    def form_valid(self, form):
        unit = form.save()
        return self.render_to_response({'unit': unit})

    def form_invalid(self, form):
        return self.render_to_response({'form': form}, status=400)

    def get_success_url(self):
        return reverse_lazy('unit_list', kwargs={'pk': self.object.property.pk})
