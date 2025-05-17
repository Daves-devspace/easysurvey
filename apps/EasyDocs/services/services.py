# views.py
from datetime import timedelta

from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.generic import UpdateView

from apps.EasyDocs.exceptions import OverrideError, BookingError, ClientServiceError
from apps.EasyDocs.models import Service, Process, SubService, ClientService, Client, Booking, ClientServiceProcess, \
    ServiceCategory
from apps.EasyDocs.forms import ServiceForm, ClientSubServiceForm
from decimal import Decimal, InvalidOperation
import logging

logger = logging.getLogger(__name__)

from django.views import View
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages

from django.http import JsonResponse
from django.urls import reverse_lazy
from django.views.generic.edit import UpdateView
from django.utils.dateformat import format as dformat

from decimal import Decimal, InvalidOperation
from django.contrib import messages
import traceback


def default_scheduled_date():
    return timezone.now().replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=1)


@transaction.atomic
def handle_ground_booking(cs, scheduled_date=None, dispatch_message=''):
    if cs.service.category != ServiceCategory.GROUND:
        return None

    scheduled_date = scheduled_date or default_scheduled_date()
    dispatch_message = dispatch_message.strip()

    booking = getattr(cs, 'ground_booking', None)
    try:
        if booking:
            booking.scheduled_date = scheduled_date
            if dispatch_message:
                booking.dispatch_message = dispatch_message
        else:
            booking = Booking.objects.create(
                client_service=cs,
                scheduled_date=scheduled_date,
                dispatch_message=dispatch_message or ''
            )

        if not booking.dispatch_message:
            booking.dispatch_message = booking.generate_default_message()

        booking.save(update_fields=['scheduled_date', 'dispatch_message'])
        return booking
    except Exception as e:
        # Optionally log here
        raise BookingError(f"Failed to create or update booking: {str(e)}")

def update_client_service_overrides(cs, data) -> None:
    pids = data.getlist('process_id[]')
    costs = data.getlist('process_cost[]')

    if pids and costs:
        # For each process_id and process_cost pair, update the overridden cost
        for pid, cost_str in zip(pids, costs):
            try:
                cost = Decimal(cost_str)
                # Fetch the related ClientServiceProcess for the given process_id
                csp = cs.service_processes.get(process_id=pid)  # Ensure you’re getting the right process
                csp.overridden_cost = cost
                csp.save(update_fields=['overridden_cost'])
            except ClientServiceProcess.DoesNotExist:
                raise OverrideError(f"Process with ID {pid} not found.")
            except InvalidOperation:
                raise OverrideError(f"Invalid cost value: {cost_str}")
    else:
        # Update the overridden total price if provided
        import logging
        logging.getLogger(__name__).info("POST keys: %s", data.keys())
        logging.getLogger(__name__).info("override_total POST value: %r", data.get('overridden_total_price'))
        otp = data.get('overridden_total_price', '').strip()
        if otp:
            try:
                cs.overridden_total_price = Decimal(otp)
            except InvalidOperation:
                raise OverrideError("Invalid override total price value.")
        else:
            cs.overridden_total_price = None


def create_client_service_with_overrides(client, service, land_description, post_data):
    try:
        # Create the ClientService object
        cs = ClientService.objects.create(
            client=client,
            service=service,
            land_description=land_description,
        )

        # Ensure that ClientServiceProcess records are created for each Process in the Service
        if service.category == ServiceCategory.TITLE:
            for process in service.processes.all():  # Accessing the processes related to the Service
                ClientServiceProcess.objects.get_or_create(
                    client_service=cs,
                    process=process
                )

        # Now we can safely update the overrides
        update_client_service_overrides(cs, post_data)

        # If category is GROUND and total_price is 0, update total price
        if service.category == ServiceCategory.GROUND and service.total_price == 0:
            service.update_total_price()

        # Update full total of the ClientService object
        cs.update_full_total()
        return cs

    except (OverrideError, Exception) as e:
        traceback.print_exc()
        raise ClientServiceError(f"Failed to create client service: {str(e)}")

# def create_client_service_with_overrides(client, service, land_description, post_data):
#     try:
#         cs = ClientService.objects.create(
#             client=client,
#             service=service,
#             land_description=land_description,
#         )
#
#         update_client_service_overrides(cs, post_data)
#
#         if service.category == ServiceCategory.GROUND and service.total_price == 0:
#             service.update_total_price()
#
#         cs.update_full_total()
#         return cs
#
#     except (OverrideError, Exception) as e:
#         raise ClientServiceError(f"Failed to create client service: {str(e)}")




class BookingUpdateView(UpdateView):
    model = Booking
    fields = ['scheduled_date', 'dispatch_message']

    def form_valid(self, form):
        # Save and grab the updated instance
        booking = form.save()

        # If AJAX, return JSON that matches your JS
        if self.request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'scheduled_date': dformat(booking.scheduled_date, 'M d, Y H:i'),
                'dispatch_message': booking.dispatch_message,
            })

        # Fallback: redirect to referring page
        return redirect(self.request.META.get('HTTP_REFERER', reverse_lazy('client_details')))

    def form_invalid(self, form):
        if self.request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'errors': form.errors}, status=400)
        return super().form_invalid(form)















# SERVICE VIEWS
def delete_subservice(request, id):
    subservice = get_object_or_404(SubService, id=id)
    subservice.delete()
    messages.success(request, "SubService deleted successfully!")
    return redirect('management')


def get_service_processes(request, service_id):
    service = get_object_or_404(Service, id=service_id)
    processes = Process.objects.filter(service_id=service_id)

    if not processes.exists():
        return JsonResponse({
            "processes": [],
            "total_price": float(service.total_price)
        })

    data = {
        "processes": [
            {
                "id": p.id,
                "name": p.name,
                "default_cost": float(p.cost)
            }
            for p in processes
        ]
    }
    return JsonResponse(data)


def services_by_category(request):
    category = request.GET.get('category')
    services = Service.objects.filter(category=category).values('id', 'name')
    return JsonResponse({'services': list(services)})


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

            # 4) Attempt to get and convert overridden_price
            raw_price = request.POST.get('overridden_price')
            overridden_price = None
            if raw_price:
                try:
                    overridden_price = Decimal(raw_price)
                    logger.info(f"[SubService] Overridden price provided: {overridden_price}")
                except InvalidOperation:
                    logger.warning(f"[SubService] Invalid overridden price: {raw_price}")
                    messages.error(request, "Invalid overridden price value.")
                    return redirect('client_details', client_id=client.id)

            try:
                # 5) Save the ClientSubService
                sub = form.save(commit=False)
                sub.client_service = client_service
                sub.overridden_price = overridden_price  # Set the overridden price
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

    # 6) Redirect back to the client detail page
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
