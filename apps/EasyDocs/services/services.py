# views.py
from datetime import timedelta

from django.contrib import messages
from django.db import transaction
from django.db.models import Prefetch
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.generic import UpdateView

from apps.EasyDocs.exceptions import OverrideError, BookingError, ClientServiceError
from apps.EasyDocs.models import Service, Process, SubService, ClientService, Client, Booking, ClientServiceProcess, \
    ServiceCategory, ClientServiceProcessAssignment
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

    Accepts either `overridden_total_price` (current field name) or
    `override_total_price` (legacy/add-modal key) for compatibility.
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
        if hasattr(data, 'get'):
            otp_raw = data.get('overridden_total_price', '')
            if otp_raw in (None, ''):
                otp_raw = data.get('override_total_price', '')
        else:
            otp_raw = data.get('overridden_total_price') or data.get('override_total_price') or ''

        otp = str(otp_raw).strip()
        if otp:
            try:
                cs.overridden_total_price = Decimal(otp)
            except (InvalidOperation, TypeError, ValueError):
                raise OverrideError("Invalid override total price value.")
        else:
            cs.overridden_total_price = None


def apply_client_service_logic(cs, service, post_data=None, is_new=False, onboarding_marked_by=None):
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

    onboarding_user_id = getattr(onboarding_marked_by, 'id', None) if onboarding_marked_by else None
    onboarding_process_ids = set()
    if post_data and hasattr(post_data, 'getlist'):
        raw_onboarding_ids = post_data.getlist('completed_at_onboarding[]')
    elif post_data and hasattr(post_data, 'get'):
        raw_onboarding_ids = post_data.get('completed_at_onboarding[]', [])
        if isinstance(raw_onboarding_ids, str):
            raw_onboarding_ids = [raw_onboarding_ids]
    else:
        raw_onboarding_ids = []

    for raw_pid in raw_onboarding_ids:
        try:
            onboarding_process_ids.add(int(raw_pid))
        except (TypeError, ValueError):
            continue

    # Small helper to fetch existing CSP process->(status, pk) mapping
    def _existing_csp_map():
        qs = ClientServiceProcess.objects.filter(client_service=cs).select_related('process')
        return {csp.process_id: {'status': csp.status, 'pk': csp.pk} for csp in qs}

    def _apply_onboarding_flags():
        if service.category != ServiceCategory.TITLE or not onboarding_process_ids:
            return 0

        marked_at = timezone.now()
        updated = ClientServiceProcess.objects.filter(
            client_service=cs,
            process_id__in=onboarding_process_ids,
        ).update(
            status='completed',
            completed_at=marked_at,
            completed_at_onboarding=True,
            onboarding_marked_by_id=onboarding_user_id,
            onboarding_marked_at=marked_at,
        )

        if updated:
            logger.info(
                "Marked %s process(es) as completed at onboarding for cs=%s",
                updated,
                cs.pk,
            )
        return updated

    def _normalize_title_workflow_statuses():
        fresh_qs = list(
            ClientServiceProcess.objects
            .filter(client_service=cs)
            .select_related('process')
            .order_by('process__step_order')
        )
        if not fresh_qs:
            return

        actionable = [csp for csp in fresh_qs if csp.status not in ('completed', 'collected')]
        if not actionable:
            return

        in_progress_rows = [csp for csp in actionable if csp.status == 'in_progress']

        if in_progress_rows:
            keep_pk = in_progress_rows[0].pk
            for csp in in_progress_rows[1:]:
                csp.status = 'pending'
                csp.save(update_fields=['status'])
                logger.info("Normalized extra in_progress -> pending for cs=%s pid=%s", cs.pk, csp.process_id)
        else:
            first_actionable = actionable[0]
            first_actionable.status = 'in_progress'
            first_actionable.save(update_fields=['status'])
            logger.info(
                "Set pid=%s to in_progress for cs=%s (after onboarding normalization)",
                first_actionable.process_id,
                cs.pk,
            )

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

        _apply_onboarding_flags()
        _normalize_title_workflow_statuses()

        # Send initial SMS for first in_progress process (after onboarding normalization)
        if is_new:
            in_progress_csp = ClientServiceProcess.objects.filter(
                client_service=cs,
                status='in_progress'
            ).order_by('process__step_order').first()
            
            if in_progress_csp and in_progress_csp.process.notification_enabled:
                # Import here to avoid circular imports
                from apps.EasyDocs.signals import send_process_sms
                
                reason = f"{service.name} – process: {in_progress_csp.process.name}"
                send_process_sms(
                    client_service=cs,
                    client=cs.client,
                    phone=cs.client.phone,
                    message=in_progress_csp.process.message,
                    reason=reason
                )
                logger.info(
                    "Sent initial SMS for cs=%s process=%s (after onboarding normalization)",
                    cs.pk,
                    in_progress_csp.process.name
                )

        has_incomplete_steps = ClientServiceProcess.objects.filter(client_service=cs).exclude(
            status__in=['completed', 'collected']
        ).exists()
        desired_status = 'active' if has_incomplete_steps else 'completed'
        if cs.status != desired_status:
            cs.status = desired_status
            cs.save(update_fields=['status'])
            logger.info("Updated ClientService status for cs=%s to '%s'", cs.pk, desired_status)
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


def create_client_service_with_overrides(client, service, land_description, post_data, onboarding_marked_by=None):
    try:
        # Wrap creation + sync in a transaction so select_for_update can be used by helper
        with transaction.atomic():
            cs = ClientService(
                client=client,
                service=service,
                land_description=land_description,
            )
            cs._suppress_initial_process_sms = True
            cs.save()

            # Now call helper (it will detect in_atomic_block and may use select_for_update)
            return apply_client_service_logic(
                cs,
                service,
                post_data,
                is_new=True,
                onboarding_marked_by=onboarding_marked_by,
            )

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


def _safe_int(raw_value):
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return None


def _build_assignee_prefill_from_client_service(client_service):
    prefill = {
        "suggested_default_assignee_id": None,
        "suggested_assignee_map": {},
    }

    if client_service is None:
        return prefill

    prefill["suggested_default_assignee_id"] = client_service.assigned_employee_id

    steps = (
        ClientServiceProcess.objects
        .filter(client_service=client_service)
        .select_related("process")
        .prefetch_related(
            Prefetch(
                "assignments",
                queryset=ClientServiceProcessAssignment.objects.filter(is_active=True).order_by("id"),
                to_attr="active_assignments",
            )
        )
    )

    for step in steps:
        assignee_ids = []
        seen = set()
        for assignment in getattr(step, "active_assignments", []):
            assignee_id = assignment.assignee_id
            if not assignee_id or assignee_id in seen:
                continue
            seen.add(assignee_id)
            assignee_ids.append(assignee_id)

        prefill["suggested_assignee_map"][str(step.process_id)] = assignee_ids

    return prefill


def _get_last_service_assignee_prefill(
    service_id,
    client_id=None,
    exclude_client_service_id=None,
    allow_global_fallback=False,
):
    empty_prefill = {
        "suggested_default_assignee_id": None,
        "suggested_assignee_map": {},
    }

    base_qs = ClientService.objects.filter(service_id=service_id)
    if exclude_client_service_id:
        base_qs = base_qs.exclude(pk=exclude_client_service_id)

    if client_id:
        local_latest = base_qs.filter(client_id=client_id).order_by("-updated_at", "-requested_at", "-id").first()
        if local_latest is not None:
            return _build_assignee_prefill_from_client_service(local_latest)

    if not allow_global_fallback:
        return empty_prefill

    global_latest = base_qs.order_by("-updated_at", "-requested_at", "-id").first()
    if global_latest is None:
        return empty_prefill

    return _build_assignee_prefill_from_client_service(global_latest)


def get_service_processes(request, service_id):
    service = get_object_or_404(Service, id=service_id)
    processes = Process.objects.filter(service_id=service_id)
    client_id = _safe_int(request.GET.get("client_id"))
    exclude_client_service_id = _safe_int(request.GET.get("exclude_client_service_id"))
    global_fallback = str(request.GET.get("global_fallback") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    prefill = _get_last_service_assignee_prefill(
        service_id=service_id,
        client_id=client_id,
        exclude_client_service_id=exclude_client_service_id,
        allow_global_fallback=global_fallback,
    )

    if not processes.exists():
        return JsonResponse({
            "processes": [],
            "total_price": float(service.total_price),
            "expected_duration_days": service.expected_duration_days,
            "suggested_default_assignee_id": prefill["suggested_default_assignee_id"],
            "suggested_assignee_map": prefill["suggested_assignee_map"],
        })

    data = {
        "processes": [
            {
                "id": p.id,
                "name": p.name,
                "default_cost": float(p.cost)
            }
            for p in processes
        ],
        "expected_duration_days": service.expected_duration_days,
        "suggested_default_assignee_id": prefill["suggested_default_assignee_id"],
        "suggested_assignee_map": prefill["suggested_assignee_map"],
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