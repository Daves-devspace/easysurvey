# views.py
from django.shortcuts import render, redirect, get_object_or_404
from .models import Service, Process
from .forms import ServiceForm

# SERVICE VIEWS
def service_list(request):
    services = Service.objects.all()
    return render(request, 'settings/service_list.html', {'services': services})

def add_service(request):
    form = ServiceForm(request.POST or None)
    if form.is_valid():
        form.save()
        return redirect('service_list')
    return render(request, 'settings/service_form.html', {'form': form, 'title': 'Add Service'})

def update_service(request, pk):
    service = get_object_or_404(Service, pk=pk)
    form = ServiceForm(request.POST or None, instance=service)
    if form.is_valid():
        form.save()
        return redirect('service_list')
    return render(request, 'settings/service_form.html', {'form': form, 'title': 'Update Service'})
