# views/actions.py
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Value, F, Sum, DecimalField
from django.db.models.functions import Coalesce, Cast
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse

from django.views import View

from apps.EasyDocs.exceptions import ClientServiceError
from apps.EasyDocs.forms import ClientSmsForm, ClientServiceForm, ClientSubServiceForm, ClientSubServiceEditForm
from apps.EasyDocs.models import Client, MessageLog, ClientService, ClientSubService, ServiceCategory
from apps.EasyDocs.services.services import create_client_service_with_overrides, \
    update_client_service_overrides, handle_ground_booking, default_scheduled_date
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

    def handle_add_client_service(self, request, client):
        form = ClientServiceForm(request.POST)
        if not form.is_valid():
            return self._handle_form_errors(form, client.id)

        try:
            cs = create_client_service_with_overrides(
                client=client,
                service=form.cleaned_data['service'],
                land_description=form.cleaned_data['land_description'],
                post_data=request.POST
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
        form = ClientServiceForm(request.POST, instance=cs)
        if not form.is_valid():
            return self._handle_form_errors(form, client.id)

        try:
            cs = form.save(commit=False)
            cs.client = client
            cs.save()

            update_client_service_overrides(cs, request.POST)
            cs.save(update_fields=['overridden_total_price'])  # ← persist the override

            book_note = self._handle_booking_service(cs, form)
            sms_note = self._sms_feedback(cs)

            messages.success(request, f"✅ Service updated successfully.{book_note}{sms_note}")
        except ClientServiceError as e:
            messages.error(request, f"❌ Failed to update service: {e}")
        return redirect('client_details', client_id=client.id)

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
        css.overridden_price = form.cleaned_data.get('overridden_price')
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