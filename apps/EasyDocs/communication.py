# apps/EasyDocs/communication.py
from datetime import datetime
import logging
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import ListView
from django.views.generic.edit import FormMixin

from .tasks import schedule_bulk_broadcast, send_employee_and_company_copy
from .forms import BulkSmsForm
from .models import MessageLog, Client, ScheduledTask
from .utils import MobileSasaAPI, clean_placeholders

logger = logging.getLogger(__name__)


def send_and_log_sms(client_service, client, phone, message, reason):
    sms_api = MobileSasaAPI()
    cache_key = 'sms_balance_check'
    cached_balance = None
    try:
        from django.core.cache import cache
        cached_balance = cache.get(cache_key)
    except Exception:
        cached_balance = None

    if cached_balance is None:
        balance_info = sms_api.get_balance()
        current_balance = balance_info.get('balance', 0)
        try:
            cache.set(cache_key, current_balance, timeout=60)
        except Exception:
            pass
        logger.debug("SMS balance fetched from API: %s", current_balance)
    else:
        current_balance = cached_balance
        logger.debug("SMS balance from cache: %s", current_balance)

    if current_balance <= 0:
        logger.warning("Insufficient SMS balance (%s)", current_balance)
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

    try:
        result = sms_api.send_sms(phone, message)
        message_id = result.get('message_id') if isinstance(result, dict) else None
        send_status = 'sent' if result.get('status') else 'failed'
        error_details = None if send_status == 'sent' else result.get('message')
        logger.info("SMS attempt to %s -> status %s", phone, send_status)
    except Exception as e:
        message_id = None
        send_status = 'failed'
        error_details = str(e)
        logger.exception("SMS send failed to %s: %s", phone, e)

    log = MessageLog.objects.create(
        client_service=client_service,
        client=client,
        phone=phone,
        message=message,
        reason=reason,
        recipient_type='client' if client else 'employee',
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

        all_logs = MessageLog.objects.all().order_by('-timestamp')
        ctx['logs'] = all_logs
        ctx['total_messages'] = all_logs.count()
        ctx['success_count'] = all_logs.filter(send_status='sent').count()
        ctx['failed_logs'] = all_logs.filter(send_status='failed')
        ctx['failed_count'] = ctx['failed_logs'].count()

        ctx['scheduled_tasks'] = ScheduledTask.objects.filter(
            status='pending',
            scheduled_time__gt=timezone.now()
        ).order_by('scheduled_time')

        ctx['previews'] = getattr(self, 'previews', [])
        ctx['employee_preview'] = getattr(self, 'employee_preview', '')
        ctx['company_preview'] = getattr(self, 'company_preview', '')

        return ctx

    def post(self, request, *args, **kwargs):
        # Cancel scheduled task
        if 'cancel' in request.POST:
            task_id = request.POST.get('log_id')
            task = ScheduledTask.objects.filter(task_id=task_id).first()
            if task and task.is_cancelable():
                from celery import current_app
                current_app.control.revoke(task.task_id, terminate=True)
                task.status = 'cancelled'
                task.save()
                messages.success(request, "Broadcast cancelled.")
            return redirect(self.success_url)

        # Retry failed SMS
        if 'retry_log_id' in request.POST:
            log_id = request.POST.get('retry_log_id')
            from .tasks import retry_failed_sms
            retry_failed_sms.delay(log_id)
            messages.success(request, "Retry initiated.")
            return redirect(self.success_url)

        form = self.get_form()
        if not form.is_valid():
            messages.error(request, "Please fix the errors before proceeding.")
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
            msg = tpl.replace('{client_first_name}', client.first_name or '')
            msg = msg.replace('{client_last_name}', client.last_name or '')
            self.previews.append({
                'client': f"{client.first_name} {client.last_name}",
                'message': msg,
                'send_at': send_at
            })

        self.employee_preview = clean_placeholders(tpl)
        self.company_preview = clean_placeholders(tpl)

    def handle_send(self, form):
        tpl = form.cleaned_data['message']
        dt = form.cleaned_data.get('scheduled_date')

        # ALWAYS pass scheduled_iso — no Celery ETA here
        scheduled_iso = dt.isoformat() if dt else None

        try:
            result = schedule_bulk_broadcast.delay(
                tpl,
                scheduled_iso=scheduled_iso
            )

            messages.success(
                self.request,
                f"Broadcast queued successfully. Task ID: {result.id}"
            )
            return redirect(self.success_url)

        except Exception as e:
            logger.exception("Failed to queue broadcast")
            messages.error(self.request, f"Error: {e}")
            return self.get(self.request)