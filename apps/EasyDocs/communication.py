from datetime import datetime
import logging
from django.contrib import messages
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import ListView
from django.views.generic.edit import FormMixin
from django.core.cache import cache

from .tasks import schedule_bulk_broadcast
from .forms import BulkSmsForm
from .models import MessageLog, Client, ScheduledTask
from .utils import MobileSasaAPI

logger = logging.getLogger(__name__)


def send_and_log_sms(client_service, client, phone, message, reason):
    """
    ✅ OPTIMIZED: Sends an SMS via MobileSasaAPI with cached balance check.
    
    Improvements:
    - Caches balance for 60 seconds to avoid repeated API calls
    - Still checks balance but reuses cached value
    - Maintains all safety checks
    
    Returns the created MessageLog instance.
    """
    sms_api = MobileSasaAPI()

    # ✅ IMPROVEMENT: Check cached balance first (valid for 60 seconds)
    cache_key = 'sms_balance_check'
    cached_balance = cache.get(cache_key)
    
    if cached_balance is None:
        # Only call API if cache is empty
        balance_info = sms_api.get_balance()
        current_balance = balance_info.get('balance', 0)
        # Cache for 60 seconds
        cache.set(cache_key, current_balance, timeout=60)
        logger.debug(f"📊 SMS balance fetched from API: {current_balance}")
    else:
        current_balance = cached_balance
        logger.debug(f"📊 SMS balance from cache: {current_balance}")
    
    if current_balance <= 0:
        logger.warning(f"⚠️ Insufficient SMS balance ({current_balance})")
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
        logger.info(f"✅ SMS sent successfully to {phone}")
    except Exception as e:
        message_id = None
        send_status = 'failed'
        error_details = str(e)
        logger.error(f"❌ SMS send failed to {phone}: {e}")

    # 3. Log the attempt
    log = MessageLog.objects.create(
        client_service=client_service,
        client=client,
        phone=phone,
        message=message,
        reason=reason,
        message_id=message_id,
        send_status=send_status,
        delivery_status='pending' if send_status == 'sent' else 'failed',
        error_details=error_details
    )
    return log


class CommunicationView(FormMixin, ListView):
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

        # History of all sent/logged messages
        all_logs = MessageLog.objects.all().order_by('-timestamp')
        ctx['logs'] = all_logs

        # Delivery summary
        ctx['total_messages'] = all_logs.count()
        ctx['success_count'] = all_logs.filter(send_status='sent').count()  # Fixed: was 'success'
        ctx['failed_logs'] = all_logs.filter(send_status='failed')
        ctx['failed_count'] = ctx['failed_logs'].count()

        # Pending scheduled Celery tasks
        ctx['scheduled_tasks'] = ScheduledTask.objects.filter(
            status='pending',
            scheduled_time__gt=timezone.now()
        ).order_by('scheduled_time')

        # Previews from handle_preview (empty by default)
        ctx['previews'] = getattr(self, 'previews', [])

        return ctx

    def post(self, request, *args, **kwargs):
        # Cancel a pending broadcast
        if 'cancel' in request.POST:
            task_id = request.POST.get('log_id')
            task = ScheduledTask.objects.filter(task_id=task_id).first()
            if task and task.is_cancelable():
                from celery import current_app
                current_app.control.revoke(task.task_id, terminate=True)
                task.status = 'cancelled'
                task.save()
                messages.success(request, "🗑️ Broadcast cancelled.")
            return redirect(self.success_url)

        if 'retry_log_id' in request.POST:
            log_id = request.POST.get('retry_log_id')
            from .tasks import retry_failed_sms
            retry_failed_sms.delay(log_id)
            messages.success(request, "🔁 Retry initiated.")
            return redirect(self.success_url)

        form = self.get_form()
        if not form.is_valid():
            messages.error(request, "❌ Please fix the errors before proceeding.")
            return self.get(request)

        if 'preview' in request.POST:
            self.handle_preview(form)
            return self.get(request)

        if 'send' in request.POST:
            return self.handle_send(form)

        return self.get(request)

    def handle_preview(self, form):
        tpl = form.cleaned_data['message']
        dt = form.cleaned_data.get('scheduled_date')
        clients = Client.objects.all()[:5]

        self.previews = []
        for client in clients:
            send_at = dt.strftime("%Y-%m-%d %H:%M") if dt else 'Now'
            message = tpl.replace('{client_first_name}', client.first_name)
            message = message.replace('{client_last_name}', client.last_name)
            self.previews.append({
                'client': f"{client.first_name} {client.last_name}",
                'message': message,
                'send_at': send_at
            })

    def handle_send(self, form):
        tpl = form.cleaned_data['message']
        dt = form.cleaned_data.get('scheduled_date')
        iso = dt.isoformat() if dt else None

        try:
            schedule_bulk_broadcast.delay(tpl, scheduled_iso=iso)
            messages.success(self.request, "✅ Messages queued successfully.")
            return redirect(self.success_url)
        except Exception as e:
            messages.error(self.request, f"❌ Error: {e}")
            return self.get(self.request)