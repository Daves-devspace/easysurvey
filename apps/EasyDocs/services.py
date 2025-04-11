# views.py
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from .models import Service, Process, SubService, ClientService, Client
from .forms import ServiceForm, ClientSubServiceForm
import logging
logger = logging.getLogger(__name__)


# SERVICE VIEWS
def delete_subservice(request, id):
    subservice = get_object_or_404(SubService, id=id)
    subservice.delete()
    messages.success(request, "SubService deleted successfully!")
    return redirect('management')



def get_service_processes(request, service_id):
    processes = Process.objects.filter(service_id=service_id)
    data = {
        "processes": [
            {
                "id": p.id,
                "name": p.name,
                "default_cost": float(p.cost)  # ← convert Decimal to float
            }
            for p in processes
        ]
    }
    return JsonResponse(data)





def add_or_update_client_subservice(request, client_id):
    """
    Utility function to add or update ClientSubService.
    Handles adding/updating subservices linked to a specific ClientService.
    """
    # 1) Fetch the Client
    client = get_object_or_404(Client, id=client_id)
    logger.info(f"[SubService] Client {client_id} found.")

    # 2) Only handle POST
    if request.method == "POST":
        form = ClientSubServiceForm(request.POST)
        logger.info(f"[SubService] Form submitted for client {client_id}. Valid? {form.is_valid()}")

        if form.is_valid():
            # 3) Pull the client_service FK from POST
            cs_id = request.POST.get('client_service')
            client_service = get_object_or_404(ClientService, id=cs_id)
            logger.info(f"[SubService] Using ClientService {cs_id} for client {client_id}.")

            try:
                # 4) Save the ClientSubService
                sub = form.save(commit=False)
                sub.client_service = client_service
                sub.save()
                logger.info(f"[SubService] Saved subservice {sub.id} for ClientService {cs_id}.")
                messages.success(request, "SubService has been successfully added/updated.")
            except Exception as e:
                logger.error(f"[SubService] Error saving subservice for client {client_id}: {e}")
                messages.error(request, "There was an issue saving the SubService.")
        else:
            logger.warning(f"[SubService] Validation errors for client {client_id}: {form.errors}")
            messages.error(request, "Please correct the errors in the SubService form.")

    else:
        logger.warning(f"[SubService] Ignored non-POST request ({request.method}) for client {client_id}.")

    # 5) Redirect back to the client detail page
    return redirect('client_details', client_id=client.id)



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


