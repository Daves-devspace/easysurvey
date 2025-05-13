# views/actions.py


from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse

from django.views import View

from apps.EasyDocs.exceptions import ClientServiceError
from apps.EasyDocs.forms import ClientSmsForm, ClientServiceForm, ClientSubServiceForm, ClientSubServiceEditForm
from apps.EasyDocs.models import Client, MessageLog, ClientService, ClientSubService, ServiceCategory
from apps.EasyDocs.services.services import create_client_service_with_overrides, \
    update_client_service_overrides, handle_ground_booking, default_scheduled_date
from apps.EasyDocs.utils import MobileSasaAPI


import logging


logger = logging.getLogger(__name__)


class ClientActionView(PermissionRequiredMixin, View):
    """
    Base for all client-scoped actions.
    Expects:
      - self.permission_required
      - self.client_lookup(request, **kwargs)
      - a post(request, client) -> None that raises or sets messages
    """
    raise_exception = True

    def dispatch(self, request, *args, **kwargs):
        # Fetch the client once for all actions
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
            # let PermissionRequiredMixin handle it
            raise
        except Exception as e:
            # Log & surface a generic error
            logger.exception(f"{self.__class__.__name__} failed: {e}")
            messages.error(request, f"❌ Error: {e}")
        return redirect(self.get_success_url())

    def handle(self, request, client):
        """
        Subclasses implement this to do their work,
        calling messages.success/error as needed.
        """
        raise NotImplementedError





#send client sms
# views/actions.py (continued)

class SendClientSMSView(ClientActionView):
    permission_required = 'easydocs.send_client_sms'

    def handle(self, request, client):
        form = ClientSmsForm(request.POST)
        if not form.is_valid():
            messages.error(request, "❌ Please enter a valid message.")
            return

        text = form.cleaned_data['message']
        resp = MobileSasaAPI().send_sms(client.phone, text)
        sent = bool(resp.get('status'))

        # Log
        MessageLog.objects.create(
            client=client,
            phone=client.phone,
            message=text,
            send_status=('sent' if sent else 'failed'),
            delivery_status=('pending' if sent else 'failed'),
            error_details=(resp.get('message') or '')
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



# class ClientServiceManageView(ClientActionView):
#     def get_permission_required(self):
#         action = self._detect_action()
#         if action == 'add':
#             return ['easydocs.add_clientservice']
#         elif action == 'edit':
#             return ['easydocs.change_clientservice']
#         return []
#
#     def _detect_action(self):
#         return 'edit' if 'client_service_id' in self.request.POST else 'add'
#
#     def handle(self, request, client):
#         action = self._detect_action()
#         if action == 'edit':
#             return self.handle_edit_client_service(request, client)
#         return self.handle_add_client_service(request, client)
#
#     def handle_add_client_service(self, request, client):
#         form = ClientServiceForm(request.POST)
#         if not form.is_valid():
#             for field, errs in form.errors.items():
#                 for err in errs:
#                     messages.error(request, f"{field}: {err}")
#             return redirect('client_details', client_id=client.id)
#
#         # Bind client and save
#         form.instance.client = client
#         cs = form.save()
#
#         # Override per-process costs
#         pids = request.POST.getlist('process_id[]')
#         costs = request.POST.getlist('process_cost[]')
#         if pids and costs:
#             for pid, cost_str in zip(pids, costs):
#                 try:
#                     cost = Decimal(cost_str)
#                     csp = cs.service_processes.get(process_id=pid)
#                     csp.overridden_cost = cost
#                     csp.save(update_fields=['overridden_cost'])
#                 except (ClientServiceProcess.DoesNotExist, InvalidOperation):
#                     continue
#         else:
#             otp = request.POST.get('override_total_price')
#             if otp:
#                 try:
#                     cs.overridden_total_price = Decimal(otp)
#                     cs.save(update_fields=['overridden_total_price'])
#                 except InvalidOperation:
#                     messages.warning(request, "⚠️ Invalid total price—ignored.")
#             elif cs.overridden_total_price is not None:
#                 cs.overridden_total_price = None
#                 cs.save(update_fields=['overridden_total_price'])
#
#         # Ground-service booking
#         svc = cs.service
#         book_note = ""
#         if svc.category == ServiceCategory.GROUND:
#             sd = form.cleaned_data.get('scheduled_date') or (datetime.now() + timedelta(days=1, hours=9))
#             msg = form.cleaned_data.get('dispatch_preview', '').strip()
#             booking = Booking.objects.create(
#                 client_service=cs,
#                 scheduled_date=sd,
#                 dispatch_message=msg or ''
#             )
#             if not booking.dispatch_message:
#                 booking.dispatch_message = booking.generate_default_message()
#                 booking.save(update_fields=['dispatch_message'])
#             book_note = f" 🗓 Scheduled for {sd.strftime('%A, %d %B %Y at %I:%M %p')}"
#
#         # SMS feedback
#         sms_log = cs.message_logs.order_by('-timestamp').first()
#         sms_note = (
#             f" 📤 SMS sent ({sms_log.reason})." if sms_log and sms_log.send_status == 'sent'
#             else f" ❌ SMS failed ({sms_log.reason})." if sms_log
#             else " ⚠️ No SMS was attempted."
#         )
#
#         messages.success(
#             request,
#             f"✅ Service assigned successfully.{book_note}{sms_note}"
#         )
#         return redirect('client_details', client_id=client.id)
#
#     def handle_edit_client_service(self, request, client):
#         cs_id = request.POST.get('client_service_id')
#         cs = get_object_or_404(ClientService, id=cs_id, client=client)
#         form = ClientServiceForm(request.POST, instance=cs)
#         if not form.is_valid():
#             for field, errs in form.errors.items():
#                 for err in errs:
#                     messages.error(request, f"{field}: {err}")
#             return redirect('client_details', client_id=client.id)
#
#         # Save changes
#         form.instance.client = client
#         cs = form.save()
#
#         # Override per-process costs
#         pids = request.POST.getlist('process_id[]')
#         costs = request.POST.getlist('process_cost[]')
#         if pids and costs:
#             for pid, cost_str in zip(pids, costs):
#                 try:
#                     cost = Decimal(cost_str)
#                     csp = cs.service_processes.get(process_id=pid)
#                     csp.overridden_cost = cost
#                     csp.save(update_fields=['overridden_cost'])
#                 except (ClientServiceProcess.DoesNotExist, InvalidOperation):
#                     continue
#         else:
#             otp = request.POST.get('override_total_price')
#             if otp:
#                 try:
#                     cs.overridden_total_price = Decimal(otp)
#                     cs.save(update_fields=['overridden_total_price'])
#                 except InvalidOperation:
#                     messages.warning(request, "⚠️ Invalid total price—ignored.")
#             elif cs.overridden_total_price is not None:
#                 cs.overridden_total_price = None
#                 cs.save(update_fields=['overridden_total_price'])
#
#         # Update/create Ground booking
#         book_note = ""
#         if cs.service.category == ServiceCategory.GROUND:
#             sd = form.cleaned_data.get('scheduled_date') or (datetime.now() + timedelta(days=1, hours=9))
#             msg = form.cleaned_data.get('dispatch_preview', '').strip()
#             booking = getattr(cs, 'ground_booking', None)
#             if booking:
#                 booking.scheduled_date = sd
#                 booking.dispatch_message = msg or booking.dispatch_message
#                 booking.save(update_fields=['scheduled_date', 'dispatch_message'])
#             else:
#                 booking = Booking.objects.create(
#                     client_service=cs,
#                     scheduled_date=sd,
#                     dispatch_message=msg or ''
#                 )
#                 if not booking.dispatch_message:
#                     booking.dispatch_message = booking.generate_default_message()
#                     booking.save(update_fields=['dispatch_message'])
#             book_note = f" 🗓 Scheduled for {sd.strftime('%A, %d %B %Y at %I:%M %p')}"
#
#         # SMS feedback
#         sms_log = cs.message_logs.order_by('-timestamp').first()
#         sms_note = (
#             f" 📤 SMS sent ({sms_log.reason})." if sms_log and sms_log.send_status == 'sent'
#             else f" ❌ SMS failed ({sms_log.reason})." if sms_log
#             else " ⚠️ No SMS was attempted."
#         )
#
#         messages.success(
#             request,
#             f"✅ Service updated successfully.{book_note}{sms_note}"
#         )
#         return redirect('client_details', client_id=client.id)





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



