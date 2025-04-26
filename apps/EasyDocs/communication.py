from datetime import datetime
import logging
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import ListView
from django.views.generic.edit import FormMixin
from django_celery_beat.models import IntervalSchedule, PeriodicTask

from .forms import BulkSmsForm
from .models import MessageLog, Client, RecurringBroadcast
from .utils import MobileSasaAPI, broadcast_sms, personalize

logger = logging.getLogger(__name__)
# utils.py


def send_and_log_sms(client_service, client, phone, message, reason):
    """
    Sends an SMS via MobileSasaAPI, checks balance first, and logs the attempt.

    Returns the created MessageLog instance.
    """
    sms_api = MobileSasaAPI()

    # 1. Check SMS balance before sending
    balance_info = sms_api.get_balance()  # {'balance': <int>} or similar
    current_balance = balance_info.get('balance', 0)
    if current_balance <= 0:
        # No balance: record failure immediately
        return MessageLog.objects.create(
            client_service=client_service,
            client=client,
            phone=phone,
            message=message,
            reason=reason,
            message_id=None,
            send_status='failed',
            delivery_status='failed',
            error_details='Insufficient SMS balance',
        )

    # 2. Attempt to send
    try:
        result = sms_api.send_sms(phone, message)
        message_id = result.get('message_id')
        send_status = 'sent'
        error_details = ''
    except Exception as e:
        message_id = None
        send_status = 'failed'
        error_details = str(e)

    # 3. Log the attempt
    log = MessageLog.objects.create(
        client_service=client_service,
        client=client,
        phone=phone,
        message=message,
        reason=reason,
        message_id=message_id,
        send_status=send_status,
        delivery_status='pending',
        error_details=error_details
    )
    return log





class CommunicationView(LoginRequiredMixin, FormMixin, ListView):
    model = MessageLog
    template_name = 'Management/comunication.html'
    context_object_name = 'logs'
    paginate_by = 50
    ordering = ['-timestamp']

    form_class = BulkSmsForm
    success_url = reverse_lazy('communication_bulk')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['form'] = self.get_form()
        ctx['recurring_broadcasts'] = RecurringBroadcast.objects.all()
        if hasattr(self, 'previews'):
            ctx['previews'] = self.previews
        return ctx

    def post(self, request, *args, **kwargs):
        # 1) Edit broadcast
        if 'edit_broadcast' in request.POST:
            return self.handle_edit_broadcast(request)

        # 2) Delete broadcast
        if 'delete_broadcast' in request.POST:
            return self.handle_delete_broadcast(request)

        # 3) Preview / Send
        form = self.get_form()
        if not form.is_valid():
            messages.error(request, "❌ Please fix the errors before proceeding.")
            return self.get(request)

        # Preview: stay on page
        if 'preview' in request.POST:
            self.handle_preview(form)
            return self.get(request)

        # Send: queue & redirect → clears the form
        if 'send' in request.POST:
            self.handle_send(form)
            return redirect(self.success_url)

        # Fallback (shouldn’t happen)
        return self.get(request)

    def handle_preview(self, form):
        tpl = form.cleaned_data['message']
        st = form.cleaned_data.get('scheduled_time')
        clients = Client.objects.all()[:3]

        self.previews = [
            {
                'client': f"{c.first_name} {c.last_name}",
                'message': personalize(tpl, c),
                'send_at': st.strftime("%Y-%m-%d %H:%M") if st else 'Now'
            }
            for c in clients
        ]

    def handle_send(self, form):
        tpl = form.cleaned_data['message']
        st = form.cleaned_data.get('scheduled_time')
        recurring = form.cleaned_data.get('recurring')

        try:
            broadcast_sms(tpl, scheduled_time=st, recurring=recurring)
            messages.success(self.request, "✅ Messages successfully queued for sending.")

            if recurring:
                RecurringBroadcast.objects.create(
                    message_template=tpl,
                    scheduled_day=(st or timezone.now()).day,
                    scheduled_time=(st or timezone.now()).time(),
                    is_active=True,
                )
                messages.success(self.request, "🔁 Recurring broadcast created.")
        except Exception as e:
            messages.error(self.request, f"❌ An error occurred: {e}")

    def handle_edit_broadcast(self, request):
        broadcast = get_object_or_404(RecurringBroadcast, pk=request.POST['broadcast_id'])
        broadcast.message_template = request.POST['message']
        scheduled_str = request.POST.get('scheduled_time')
        if scheduled_str:
            dt = datetime.strptime(scheduled_str, "%Y-%m-%dT%H:%M")
            broadcast.scheduled_day = dt.day
            broadcast.scheduled_time = dt.time()
        broadcast.save()
        messages.success(request, "✅ Recurring broadcast updated.")
        return redirect(self.success_url)

    def handle_delete_broadcast(self, request):
        broadcast = get_object_or_404(RecurringBroadcast, pk=request.POST['broadcast_id'])
        broadcast.delete()
        messages.success(request, "🗑️ Recurring broadcast deleted.")
        return redirect(self.success_url)
