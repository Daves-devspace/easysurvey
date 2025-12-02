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
from django.urls import reverse_lazy, reverse
from django.views.generic.edit import UpdateView
from django.utils.dateformat import format as dformat
from django.db import transaction as db_transaction
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

    try:
        booking = getattr(cs, 'ground_booking', None)

        if booking:
            booking.scheduled_date = scheduled_date
            if dispatch_message:
                booking.dispatch_message = dispatch_message
            booking.save()  # Only one save — model handles default message and signal
        else:
            booking = Booking.objects.create(
                client_service=cs,
                scheduled_date=scheduled_date,
                dispatch_message=dispatch_message
            )

        return booking
    except Exception as e:
        raise BookingError(f"Failed to create or update booking: {str(e)}")






def update_client_service_overrides(cs, data) -> None:
    """
    Updates per-process overridden_cost if process_id[] and process_cost[] are posted,
    otherwise updates overridden_total_price.
    """
    pids = data.getlist('process_id[]') if hasattr(data, 'getlist') else data.get('process_id[]', [])
    costs = data.getlist('process_cost[]') if hasattr(data, 'getlist') else data.get('process_cost[]', [])

    if pids and costs:
        for pid, cost_str in zip(pids, costs):
            try:
                cost = Decimal(cost_str)
            except (InvalidOperation, TypeError, ValueError):
                raise OverrideError(f"Invalid cost value: {cost_str!r}")

            # Only update if a CSP exists for this process
            try:
                csp = cs.service_processes.get(process_id=pid)
            except ClientServiceProcess.DoesNotExist:
                logger.debug("Override: no CSP found for cs=%s pid=%s; skipping", cs.pk, pid)
                continue

            csp.overridden_cost = cost
            csp.save(update_fields=['overridden_cost'])
            logger.debug("Applied override for cs=%s pid=%s cost=%s", cs.pk, pid, cost)
    else:
        otp = data.get('overridden_total_price', '').strip() if hasattr(data, 'get') else (data.get('overridden_total_price') or '').strip()
        if otp:
            try:
                cs.overridden_total_price = Decimal(otp)
            except (InvalidOperation, TypeError, ValueError):
                raise OverrideError("Invalid override total price value.")
        else:
            cs.overridden_total_price = None


def apply_client_service_logic(cs, service, post_data=None, is_new=False):
    """
    Sync ClientServiceProcess rows and statuses.

    - If is_new True (creation or service changed): rebuild CSPs from service.processes and
      set first -> in_progress, others -> pending.
    - If is_new False (same service edit): use posted process_id[] as truth, preserve statuses
      for kept processes, create new with pending, normalize to exactly one in_progress.
    """
    logger.info("apply_client_service_logic START: cs=%s service=%s is_new=%s",
                getattr(cs, 'pk', None), getattr(service, 'pk', None), is_new)

    # Refresh cs/service; try to lock cs if we're in an atomic block.
    try:
        in_tx = transaction.get_connection().in_atomic_block
    except Exception:
        in_tx = False

    if in_tx and getattr(cs, 'pk', None):
        # safe to select_for_update only inside transaction
        try:
            cs = ClientService.objects.select_for_update().get(pk=cs.pk)
            logger.debug("Locked ClientService %s for update", cs.pk)
        except Exception:
            # fall back to provided cs instance if lock fails
            logger.debug("Could not select_for_update ClientService %s", getattr(cs, 'pk', None))

    try:
        service.refresh_from_db()
    except Exception:
        logger.debug("service.refresh_from_db() skipped for service=%s", getattr(service, 'pk', None))

    # Small helper to fetch existing CSP process->(status, pk) mapping
    def _existing_csp_map():
        qs = ClientServiceProcess.objects.filter(client_service=cs).select_related('process')
        return {csp.process_id: {'status': csp.status, 'pk': csp.pk} for csp in qs}

    if service.category == ServiceCategory.TITLE:
        # Get ordered process ids for deterministic behaviour
        ordered_service_procs = list(service.processes.order_by('step_order').values_list('id', flat=True))

        if is_new:
            # Rebuild from scratch: remove old rows and create new ones with
            # first->in_progress, others->pending
            logger.info("is_new=True → rebuilding CSPs for cs=%s from service=%s", cs.pk, service.pk)
            ClientServiceProcess.objects.filter(client_service=cs).delete()

            to_create = ordered_service_procs
            created_csp_ids = []
            for idx, pid in enumerate(to_create):
                try:
                    proc = Process.objects.get(pk=pid)
                except Process.DoesNotExist:
                    logger.warning("Process %s declared on service %s not found; skipping", pid, service.pk)
                    continue
                status = 'in_progress' if idx == 0 else 'pending'
                csp = ClientServiceProcess.objects.create(client_service=cs, process=proc, status=status)
                created_csp_ids.append(csp.pk)
                logger.info("Created CSP pk=%s for cs=%s proc=%s status=%s", csp.pk, cs.pk, pid, status)

        else:
            # Same service edit: use posted list as authoritative (defensive: only those belonging to service)
            posted_pids = set()
            if post_data and hasattr(post_data, 'getlist'):
                try:
                    posted_pids = [int(x) for x in post_data.getlist('process_id[]') if x]
                except Exception:
                    posted_pids = []

            # Keep order according to service definition
            desired_ordered = [pid for pid in ordered_service_procs if pid in posted_pids]
            logger.info("Edit same service: desired ordered pids=%s (posted=%s)", desired_ordered, posted_pids)

            existing_map = _existing_csp_map()

            desired_set = set(desired_ordered)
            existing_set = set(existing_map.keys())

            to_remove = existing_set - desired_set
            to_add = [pid for pid in desired_ordered if pid not in existing_set]

            if to_remove:
                ClientServiceProcess.objects.filter(client_service=cs, process_id__in=to_remove).delete()
                logger.info("Removed CSPs for cs=%s: %s", cs.pk, sorted(to_remove))

            # Create missing ones (new rows default to pending)
            for pid in to_add:
                try:
                    proc = service.processes.get(pk=pid)
                except Process.DoesNotExist:
                    logger.warning("Process %s not found while adding to cs=%s; skipping", pid, cs.pk)
                    continue
                csp = ClientServiceProcess.objects.create(client_service=cs, process=proc, status='pending')
                logger.info("Added new CSP pk=%s for cs=%s proc=%s status=pending", csp.pk, cs.pk, pid)

            # At this point, we have the desired set of CSP rows. Now ensure statuses are sensible:
            # - Preserve existing 'completed' rows.
            # - Preserve existing 'in_progress' if present.
            # - Ensure at most one 'in_progress'. If none, set first non-completed to in_progress.
            # Re-fetch fresh statuses
            fresh_qs = list(ClientServiceProcess.objects.filter(client_service=cs).select_related('process').order_by('process__step_order'))
            statuses = {csp.process_id: csp for csp in fresh_qs}

            # Count current in_progress
            in_progress_pids = [pid for pid, csp in statuses.items() if csp.status == 'in_progress']
            if len(in_progress_pids) > 1:
                # Keep the earliest in order, mark others pending
                keep = None
                for csp in fresh_qs:
                    if csp.status == 'in_progress' and keep is None:
                        keep = csp.process_id
                        continue
                    if csp.status == 'in_progress' and csp.process_id != keep:
                        csp.status = 'pending'
                        csp.save(update_fields=['status'])
                        logger.info("Normalized extra in_progress -> pending for cs=%s pid=%s", cs.pk, csp.process_id)

            # If no in_progress, make sure to set first non-completed to in_progress
            in_progress_exists = ClientServiceProcess.objects.filter(client_service=cs, status='in_progress').exists()
            if not in_progress_exists:
                # find first in order that's not completed
                for csp in fresh_qs:
                    if csp.status != 'completed':
                        csp.status = 'in_progress'
                        csp.save(update_fields=['status'])
                        logger.info("Set pid=%s to in_progress for cs=%s (no existing in_progress)", csp.process_id, cs.pk)
                        break
                    # otherwise continue until first non-completed
    else:
        # Non-title service: clear any CSP rows
        deleted_count, _ = ClientServiceProcess.objects.filter(client_service=cs).delete()
        if deleted_count:
            logger.info("Cleared %d CSPs for non-TITLE service on cs=%s", deleted_count, cs.pk)

    # Apply overrides if provided
    if post_data:
        try:
            update_client_service_overrides(cs, post_data)
        except OverrideError as oe:
            logger.warning("OverrideError applying overrides for cs=%s: %s", cs.pk, oe)
        except Exception:
            logger.exception("Unexpected error applying overrides for cs=%s", cs.pk)

    # Persist overridden_total_price if it's been set
    if cs.overridden_total_price is not None:
        try:
            cs.save(update_fields=['overridden_total_price'])
        except Exception:
            logger.exception("Failed to persist overridden_total_price for cs=%s", cs.pk)

    # Recalculate totals (use safe methods you already have)
    try:
        cs.update_full_total()
    except Exception:
        try:
            cs.recalculate_full_total_price()
            cs.save(update_fields=['full_total_price'])
        except Exception:
            logger.exception("Failed fallback recalc for cs=%s; final save()", cs.pk)
            try:
                cs.save()
            except Exception:
                logger.exception("Final save failed for cs=%s", cs.pk)

    logger.info("apply_client_service_logic COMPLETE for cs=%s", cs.pk)
    return cs







# def update_client_service_overrides(cs, data) -> None:
#     pids = data.getlist('process_id[]')
#     costs = data.getlist('process_cost[]')

#     if pids and costs:
#         # For each process_id and process_cost pair, update the overridden cost
#         for pid, cost_str in zip(pids, costs):
#             try:
#                 cost = Decimal(cost_str)
#                 # Fetch the related ClientServiceProcess for the given process_id
#                 csp = cs.service_processes.get(process_id=pid)  # Ensure you’re getting the right process
#                 csp.overridden_cost = cost
#                 csp.save(update_fields=['overridden_cost'])
#             except ClientServiceProcess.DoesNotExist:
#                 raise OverrideError(f"Process with ID {pid} not found.")
#             except InvalidOperation:
#                 raise OverrideError(f"Invalid cost value: {cost_str}")
#     else:
#         # Update the overridden total price if provided
#         import logging
#         logging.getLogger(__name__).info("POST keys: %s", data.keys())
#         logging.getLogger(__name__).info("override_total POST value: %r", data.get('overridden_total_price'))
#         otp = data.get('overridden_total_price', '').strip()
#         if otp:
#             try:
#                 cs.overridden_total_price = Decimal(otp)
#             except InvalidOperation:
#                 raise OverrideError("Invalid override total price value.")
#         else:
#             cs.overridden_total_price = None


def create_client_service_with_overrides(client, service, land_description, post_data):
    try:
        # Wrap creation + sync in a transaction so select_for_update can be used by helper
        with transaction.atomic():
            cs = ClientService.objects.create(
                client=client,
                service=service,
                land_description=land_description,
            )

            # Now call helper (it will detect in_atomic_block and may use select_for_update)
            return apply_client_service_logic(cs, service, post_data, is_new=True)

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


# def service_list(request):
#     services = Service.objects.all()
#     return render(request, 'settings/service_list.html', {'services': services})
#
#
# def add_service(request):
#     form = ServiceForm(request.POST or None)
#     if form.is_valid():
#         form.save()
#         return redirect('service_list')
#     return render(request, 'settings/service_form.html', {'form': form, 'title': 'Add Service'})
#
#
# def update_service(request, pk):
#     service = get_object_or_404(Service, pk=pk)
#     form = ServiceForm(request.POST or None, instance=service)
#     if form.is_valid():
#         form.save()
#         return redirect('service_list')
#     return render(request, 'settings/service_form.html', {'form': form, 'title': 'Update Service'})