# views/actions.py
from datetime import timedelta
from decimal import Decimal
import re

from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Value, F, Sum, DecimalField
from django.db.models.functions import Coalesce, Cast
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone

from django.views import View

from apps.EasyDocs.exceptions import ClientServiceError
from apps.EasyDocs.forms import ClientSmsForm, ClientServiceForm, ClientSubServiceForm, ClientSubServiceEditForm
from apps.EasyDocs.models import Client, MessageLog, ClientService, ClientSubService, ServiceCategory, ServiceAssignmentLog
from apps.EasyDocs.services.services import create_client_service_with_overrides, \
    apply_client_service_logic, handle_ground_booking, default_scheduled_date
from apps.EasyDocs.services.process_assignments import (
    sync_service_assignment_to_process_assignments,
    handle_assign_users_to_process_step,
)
from apps.EasyDocs.services.feature_flags import is_service_tracking_enabled, is_task_assigning_enabled
from apps.EasyDocs.utils import MobileSasaAPI

from django.contrib.auth.decorators import login_required, permission_required     
import logging


logger = logging.getLogger(__name__)


class ClientActionView(PermissionRequiredMixin, View):
    """
    Base for all client-scoped actions.
    Expects:
      - self.permission_required
      - self.client_lookup(request, **kwargs)
      - a handle(request, client) -> None that raises or sets messages
    """
    raise_exception = True

    def dispatch(self, request, *args, **kwargs):
        self.client = get_object_or_404(Client, pk=kwargs['client_id'])
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        referer = self.request.META.get('HTTP_REFERER')
        if referer:
            return referer
        return reverse('client_details', kwargs={'client_id': self.client.id})

    def post(self, request, *args, **kwargs):
        try:
            self.handle(request, self.client)
        except PermissionDenied:
            raise
        except Exception as e:
            logger.exception(f"{self.__class__.__name__} failed: {e}")
            messages.error(request, f"❌ Error: {e}")
        return redirect(self.get_success_url())

    def handle(self, request, client):
        raise NotImplementedError


class SendClientSMSView(ClientActionView):
    permission_required = 'easydocs.send_client_sms'

    def handle(self, request, client):
        form = ClientSmsForm(request.POST)
        if not form.is_valid():
            messages.error(request, "❌ Please enter a valid message.")
            return  # Will trigger redirect with error message

        text = form.cleaned_data['message']
        resp = MobileSasaAPI().send_sms(client.phone, text)
        sent = bool(resp.get('status'))

        MessageLog.objects.create(
            client=client,
            phone=client.phone,
            message=text,
            send_status='sent' if sent else 'failed',
            delivery_status='pending' if sent else 'failed',
            error_details=resp.get('message', '')
        )

        if sent:
            messages.success(request, "✅ SMS sent successfully.")
        else:
            messages.error(request, f"❌ SMS failed: {resp.get('message', 'Unknown')}")





#addclientservice


# views.py

class ClientServiceManageView(View):
    PROCESS_ASSIGNEE_KEY_RE = re.compile(r"^process_assignees_(\d+)(?:\[\])?$")

    _permission_map = {
        'add': ['easydocs.add_clientservice'],
        'edit': ['easydocs.change_clientservice'],
    }

    def get_permission_required(self):
        return self._permission_map.get(self._detect_action(), [])

    def post(self, request, client_id):
        client = get_object_or_404(ClientService.client.field.related_model, id=client_id)
        action = self._detect_action(request)
        if action == 'edit':
            return self.handle_edit_client_service(request, client)
        return self.handle_add_client_service(request, client)

    def _detect_action(self, request):
        return 'edit' if request.POST.get('client_service_id') else 'add'

    def _extract_process_assignee_map(self, post_data):
        if not hasattr(post_data, 'keys'):
            return {}

        assignee_map = {}

        for key in post_data.keys():
            match = self.PROCESS_ASSIGNEE_KEY_RE.match(key)
            if not match:
                continue

            process_id = int(match.group(1))
            raw_values = post_data.getlist(key) if hasattr(post_data, 'getlist') else post_data.get(key, [])
            if isinstance(raw_values, str):
                raw_values = [raw_values]

            seen = set()
            user_ids = []
            for raw in raw_values:
                try:
                    user_id = int(raw)
                except (TypeError, ValueError):
                    continue
                if user_id <= 0 or user_id in seen:
                    continue
                seen.add(user_id)
                user_ids.append(user_id)

            assignee_map[process_id] = user_ids

        return assignee_map

    @staticmethod
    def _derive_primary_assignee(process_assignee_map):
        all_ids = []
        for process_id in sorted(process_assignee_map.keys()):
            all_ids.extend(process_assignee_map.get(process_id, []))

        if not all_ids:
            return None

        users = User.objects.filter(id__in=all_ids)
        user_lookup = {user.id: user for user in users}

        for process_id in sorted(process_assignee_map.keys()):
            for user_id in process_assignee_map.get(process_id, []):
                user = user_lookup.get(user_id)
                if user is not None:
                    return user
        return None

    def _apply_process_level_assignments(self, request, cs, process_assignee_map, fallback_assignee=None, assignment_reason=''):
        if not is_task_assigning_enabled():
            return

        steps = list(cs.service_processes.select_related('process').all())
        if not steps:
            return

        assigned_by = request.user if request.user.is_authenticated else None
        reason = assignment_reason or 'Configured from service modal'

        for step in steps:
            target_user_ids = process_assignee_map.get(step.process_id)

            if target_user_ids is None:
                if fallback_assignee is None:
                    continue
                target_user_ids = [fallback_assignee.id]

            result = handle_assign_users_to_process_step(
                process_step_id=step.id,
                user_ids=target_user_ids,
                assigned_by=assigned_by,
                reason=reason,
            )

            if not result.get('success'):
                logger.warning(
                    "Failed to apply process assignees for cs=%s step=%s: %s",
                    cs.id,
                    step.id,
                    result.get('message'),
                )

    def handle_add_client_service(self, request, client):
        form = ClientServiceForm(request.POST)
        if not form.is_valid():
            return self._handle_form_errors(form, client.id)

        task_assigning_enabled = is_task_assigning_enabled()
        process_assignee_map = self._extract_process_assignee_map(request.POST)
        service = form.cleaned_data.get('service')
        service_has_processes = bool(service and service.processes.exists())

        assigned_employee = form.cleaned_data.get('assigned_employee')
        if task_assigning_enabled and assigned_employee is None and service_has_processes:
            assigned_employee = self._derive_primary_assignee(process_assignee_map)
            if assigned_employee is not None:
                form.cleaned_data['assigned_employee'] = assigned_employee

        if task_assigning_enabled and not assigned_employee:
            if service_has_processes:
                form.add_error('assigned_employee', 'Assign at least one employee to a process step.')
            else:
                form.add_error('assigned_employee', 'Please assign an employee before saving this service.')
            return self._handle_form_errors(form, client.id)

        try:
            cs = create_client_service_with_overrides(
                client=client,
                service=form.cleaned_data['service'],
                land_description=form.cleaned_data['land_description'],
                post_data=request.POST,
                onboarding_marked_by=request.user if request.user.is_authenticated else None,
            )

            self._apply_assignment_and_deadline(
                request=request,
                cs=cs,
                form=form,
                previous_assigned_employee=None,
                is_new=True,
            )

            book_note = self._handle_booking_service(cs, form)
            sms_note = self._sms_feedback(cs)

            messages.success(request, f"✅ Service assigned successfully.{book_note}{sms_note}")
        except ClientServiceError as e:
            messages.error(request, f"❌ Failed to assign service: {e}")
        return redirect('client_details', client_id=client.id)

    def handle_edit_client_service(self, request, client):
        cs_id = request.POST.get('client_service_id')
        cs = get_object_or_404(ClientService, id=cs_id, client=client)
        previous_assigned_employee = cs.assigned_employee
        form = ClientServiceForm(request.POST, instance=cs)
        if not form.is_valid():
            return self._handle_form_errors(form, client.id)

        try:
            task_assigning_enabled = is_task_assigning_enabled()
            # detect service change BEFORE saving so we know whether to rebuild CSPs
            new_service = form.cleaned_data['service']
            service_changed = cs.service_id != new_service.id
            process_assignee_map = self._extract_process_assignee_map(request.POST)

            assigned_employee = form.cleaned_data.get('assigned_employee')
            if task_assigning_enabled and assigned_employee is None and new_service.processes.exists():
                assigned_employee = self._derive_primary_assignee(process_assignee_map)
                if assigned_employee is not None:
                    form.cleaned_data['assigned_employee'] = assigned_employee

            if task_assigning_enabled and assigned_employee is None and new_service.processes.exists():
                form.add_error('assigned_employee', 'Assign at least one employee to a process step.')
                return self._handle_form_errors(form, client.id)

            # Save the basic ClientService changes
            cs = form.save(commit=False)
            cs.client = client
            cs.save()

            # IMPORTANT: call the helper so CSPs are rebuilt/synced
            # If the service changed, treat as "new" so helper clears old CSPs and rebuilds
            apply_client_service_logic(
                cs,
                new_service,
                post_data=request.POST,
                is_new=service_changed,
                onboarding_marked_by=request.user if request.user.is_authenticated else None,
            )

            self._apply_assignment_and_deadline(
                request=request,
                cs=cs,
                form=form,
                previous_assigned_employee=previous_assigned_employee,
                is_new=False,
            )

            # Use the correct booking helper name (this is the fix for your AttributeError)
            book_note = self._handle_booking_service(cs, form)
            sms_note = self._sms_feedback(cs)

            messages.success(request, f"✅ Service updated successfully.{book_note}{sms_note}")
            logger.info("Edited ClientService id=%s service_changed=%s", cs.pk, service_changed)

        except ClientServiceError as e:
            messages.error(request, f"❌ Failed to update service: {e}")
        except Exception as e:
            # keep a broad except while debugging to log unexpected errors
            logger.exception("Unexpected error in handle_edit_client_service: %s", e)
            messages.error(request, f"❌ Failed to update service: {e}")

        return redirect('client_details', client_id=client.id)

    def _apply_assignment_and_deadline(self, request, cs, form, previous_assigned_employee=None, is_new=False):
        assigned_employee = form.cleaned_data.get('assigned_employee')
        process_assignee_map = self._extract_process_assignee_map(request.POST)

        if assigned_employee is None and process_assignee_map:
            assigned_employee = self._derive_primary_assignee(process_assignee_map)

        service_tracking_enabled = is_service_tracking_enabled()
        configured_duration = None
        if service_tracking_enabled:
            configured_duration = form.cleaned_data.get('expected_duration_days') or cs.service.expected_duration_days

        update_fields = []
        assignment_action = None
        assignment_reason = (request.POST.get('assignment_reason') or '').strip()

        # Assignment handling
        new_assignee_id = assigned_employee.id if assigned_employee else None
        if cs.assigned_employee_id != new_assignee_id:
            cs.assigned_employee = assigned_employee
            update_fields.append('assigned_employee')

        if assigned_employee:
            if previous_assigned_employee and previous_assigned_employee != assigned_employee:
                cs.assignment_status = 'reassigned'
                assignment_action = 'reassigned'
                update_fields.append('assignment_status')
            elif not previous_assigned_employee:
                cs.assignment_status = 'pending_acceptance'
                assignment_action = 'assigned'
                update_fields.append('assignment_status')
            elif cs.assignment_status in ('unassigned', 'declined'):
                cs.assignment_status = 'pending_acceptance'
                update_fields.append('assignment_status')
        else:
            cs.assignment_status = 'unassigned'
            update_fields.append('assignment_status')

        # Duration and deadline handling
        if not service_tracking_enabled:
            if cs.expected_duration_days is not None:
                cs.expected_duration_days = None
                update_fields.append('expected_duration_days')
            if cs.deadline is not None:
                cs.deadline = None
                update_fields.append('deadline')
            if cs.original_deadline is not None:
                cs.original_deadline = None
                update_fields.append('original_deadline')
        elif configured_duration:
            if cs.expected_duration_days != configured_duration:
                cs.expected_duration_days = configured_duration
                update_fields.append('expected_duration_days')

            if not cs.deadline or is_new:
                base_date = cs.requested_at or timezone.now()
                computed_deadline = base_date + timedelta(days=configured_duration)
                cs.deadline = computed_deadline
                update_fields.append('deadline')

                if not cs.original_deadline:
                    cs.original_deadline = computed_deadline
                    update_fields.append('original_deadline')

        if update_fields:
            cs.save(update_fields=sorted(set(update_fields)))

        if assignment_action:
            ServiceAssignmentLog.objects.create(
                client_service=cs,
                assigned_employee=assigned_employee,
                previous_employee=previous_assigned_employee if assignment_action == 'reassigned' else None,
                action=assignment_action,
                assigned_by=request.user if request.user.is_authenticated else None,
                reason=assignment_reason,
            )

        try:
            if not is_task_assigning_enabled():
                return

            sync_service_assignment_to_process_assignments(
                client_service=cs,
                assigned_employee=assigned_employee,
                assigned_by=request.user if request.user.is_authenticated else None,
                reason=assignment_reason,
            )

            self._apply_process_level_assignments(
                request=request,
                cs=cs,
                process_assignee_map=process_assignee_map,
                fallback_assignee=assigned_employee,
                assignment_reason=assignment_reason,
            )
        except Exception as exc:
            logger.exception(
                "Failed to sync process assignments for ClientService %s: %s",
                cs.id,
                exc,
            )

    def _handle_form_errors(self, form, client_id):
        for field, errs in form.errors.items():
            for err in errs:
                messages.error(self.request, f"{field}: {err}")
        return redirect('client_details', client_id=client_id)

    def _handle_booking_service(self, cs, form):
        if cs.service.category != ServiceCategory.GROUND:
            return ''

        scheduled_date = form.cleaned_data.get('scheduled_date') or default_scheduled_date()
        dispatch_message = form.cleaned_data.get('dispatch_preview', '')
        booking = handle_ground_booking(cs, scheduled_date, dispatch_message)

        return f" 🗓 Scheduled for {booking.scheduled_date.strftime('%A, %d %B %Y at %I:%M %p')}" if booking else ''

    def _sms_feedback(self, cs):
        sms_log = cs.message_logs.order_by('-timestamp').first()
        if not sms_log:
            return " ⚠️ No SMS was attempted."
        if sms_log.send_status == 'sent':
            return f" 📤 SMS sent ({sms_log.reason})."
        return f" ❌ SMS failed ({sms_log.reason})."






#delete
class DeleteClientServiceView(ClientActionView):
    permission_required = 'easydocs.delete_clientservice'

    def handle(self, request, client):
        cs_id = request.POST.get('client_service_id')
        try:
            cs = ClientService.objects.get(id=cs_id, client=client)
            cs.delete()
            messages.success(request, "🗑️ Client service deleted.")
        except ClientService.DoesNotExist:
            messages.error(request, "⚠️ Client service not found.")


#add subservice
class AddClientSubserviceView(ClientActionView):
    permission_required = 'easydocs.add_clientsubservice'

    def handle(self, request, client):
        form = ClientSubServiceForm(request.POST)
        if not form.is_valid():
            messages.error(request, "❌ Error adding subservice.")
            # Display specific field errors
            for field, errs in form.errors.items():
                for err in errs:
                    messages.error(request, f"{field}: {err}")
            return

        css = form.save(commit=False)
        css.client_service = get_object_or_404(
            ClientService, id=request.POST['client_service'], client=client
        )
        css.overridden_price = form.cleaned_data.get('overridden_price') or None
        css.save()
        messages.success(request, "✅ SubService added successfully.")


#edit subservice
class EditClientSubserviceView(ClientActionView):
    permission_required = 'easydocs.change_clientsubservice'
    def handle(self, request, client):
        css = get_object_or_404(ClientSubService, id=request.POST['client_subservice_id'], client_service__client=client)
        form = ClientSubServiceEditForm(request.POST, instance=css)
        if not form.is_valid():
            messages.error(request, "❌ Error updating subservice.")
            return
        form.save()
        messages.success(request, "✅ SubService updated successfully.")


#delete subservice
class DeleteClientSubserviceView(ClientActionView):
    permission_required = 'easydocs.delete_clientsubservice'
    def handle(self, request, client):
        try:
            css = ClientSubService.objects.get(id=request.POST['client_subservice_id'], client_service__client=client)
            css.delete()
            messages.success(request, "🗑️ SubService deleted.")
        except ClientSubService.DoesNotExist:
            messages.error(request, "⚠️ SubService not found.")
            
            
     
            

@login_required
@permission_required('yourapp.change_clientsubservice', raise_exception=True)
def soft_delete_client_subservice(request, pk):
    """
    Soft-delete a ClientSubService instance.
    """
    cs = get_object_or_404(ClientSubService.objects.select_related('client_service'), pk=pk)
    try:
        cs.soft_delete(clear_price=True, force=False)
        messages.success(request, "Client subservice soft-deleted.")
    except ValueError as e:
        messages.error(request, str(e))
    return redirect(request.META.get('HTTP_REFERER', 'management'))

@login_required
@permission_required('yourapp.change_clientsubservice', raise_exception=True)
def restore_client_subservice(request, pk):
    """
    Restore a previously soft-deleted ClientSubService.
    Uses all_objects manager so inactive rows can be found.
    """
    cs = get_object_or_404(ClientSubService.all_objects.select_related('client_service'), pk=pk)
    if cs.is_active:
        messages.info(request, "Client subservice is already active.")
        return redirect(request.META.get('HTTP_REFERER', 'management'))
    cs.restore()
    messages.success(request, "Client subservice restored.")
    return redirect(request.META.get('HTTP_REFERER', 'management'))

@login_required
@permission_required('yourapp.delete_clientsubservice', raise_exception=True)
def hard_delete_client_subservice(request, pk):
    """
    Permanently delete a ClientSubService from DB. Use with care.
    """
    cs = get_object_or_404(ClientSubService.all_objects.select_related('client_service'), pk=pk)
    cs.hard_delete()
    messages.success(request, "Client subservice permanently deleted.")
    return redirect(request.META.get('HTTP_REFERER', 'management'))



def get_client_service_summary(client):
    qs = ClientService.objects.filter(client=client)

    active_count = qs.filter(status='active').count()
    completed_count = qs.filter(status__in=['completed', 'collected']).count()

    # Sum of all service totals
    total_charged = qs.aggregate(
        total=Coalesce(Sum('full_total_price'), Value(0), output_field=DecimalField())
    )['total'] or Decimal('0.00')

    # Sum of all payments related to the client's services
    total_paid = qs.aggregate(
        total=Coalesce(Sum('payments__amount'), Value(0), output_field=DecimalField())
    )['total'] or Decimal('0.00')

    total_balance = total_charged - total_paid

    return {
        'active_services': active_count,
        'completed_services': completed_count,
        'total_balance': total_balance,
    }