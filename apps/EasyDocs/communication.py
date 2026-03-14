import logging
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import ListView
from django.views.generic.edit import FormMixin
from django.db.models import Count, Q
from django.core.cache import cache
from .tasks import schedule_bulk_broadcast
from .forms import BulkSmsForm
from .models import MessageLog, Client, ScheduledTask
from .utils import MobileSasaAPI, clean_placeholders

logger = logging.getLogger(__name__)

def send_and_log_sms(client_service, client, phone, message, reason):
    if not message or not str(message).strip():
        return MessageLog.objects.create(
            client_service=client_service, client=client, phone=phone, message=message,
            reason=reason, send_status='failed', delivery_status='failed', error_details='Empty message payload'
        )

    sms_api = MobileSasaAPI()
    
    cache_key = 'sms_balance_check'
    cached_balance = cache.get(cache_key)

    if cached_balance == '__auth_error__':
        logger.warning("Skipping SMS send because provider auth is currently invalid.")
        return MessageLog.objects.create(
            client_service=client_service, client=client, phone=phone, message=message,
            reason=reason, send_status='failed', delivery_status='failed', error_details='SMS provider unauthorized'
        )
    
    if cached_balance is None:
        balance_info = sms_api.get_balance()
        if isinstance(balance_info, dict) and balance_info.get('auth_error'):
            cache.set(cache_key, '__auth_error__', timeout=60)
            logger.error("SMS provider auth error while checking balance; blocking send.")
            return MessageLog.objects.create(
                client_service=client_service, client=client, phone=phone, message=message,
                reason=reason, send_status='failed', delivery_status='failed', error_details='SMS provider unauthorized'
            )

        current_balance = balance_info.get('balance', 0) if isinstance(balance_info, dict) else 0
        cache.set(cache_key, current_balance, timeout=60)
    else:
        current_balance = cached_balance

    if current_balance is not None and float(current_balance) <= 0:
        logger.warning("Insufficient SMS balance")
        return MessageLog.objects.create(
            client_service=client_service, client=client, phone=phone, message=message,
            reason=reason, send_status='failed', delivery_status='failed', error_details='Insufficient SMS balance'
        )

    try:
        result = sms_api.send_sms(phone, message)
        message_id = result.get('message_id') if isinstance(result, dict) else None
        status_bool = result.get('status', False)
        
        send_status = 'sent' if status_bool else 'failed'
        delivery_status = 'pending' if status_bool else 'failed'
        error_details = None if status_bool else result.get('message', 'Unknown API Error')
    except Exception as e:
        message_id = None
        send_status = 'failed'
        delivery_status = 'failed'
        error_details = str(e)

    log = MessageLog.objects.create(
        client_service=client_service, client=client, phone=phone, message=message,
        reason=reason, recipient_type='client' if client else 'employee',
        message_id=message_id, send_status=send_status, delivery_status=delivery_status, error_details=error_details
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

    CACHE_TIMEOUT = 60 * 5
    CACHE_COUNTS_TIMEOUT = 60 * 2

    def get_queryset(self):
        return MessageLog.objects.select_related('client').order_by('-timestamp')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['form'] = self.get_form()

        counts = cache.get('messagelog_counts')
        if not counts:
            counts = MessageLog.objects.aggregate(
                total=Count('id'),
                success_count=Count('id', filter=Q(send_status='sent')),
                failed_count=Count('id', filter=Q(send_status='failed'))
            )
            cache.set('messagelog_counts', counts, self.CACHE_COUNTS_TIMEOUT)

        ctx.update({
            'total_messages': counts['total'],
            'success_count': counts['success_count'],
            'failed_count': counts['failed_count'],
        })

        scheduled_tasks = ScheduledTask.objects.filter(
            status='pending', scheduled_time__gt=timezone.now()
        ).order_by('scheduled_time')[:50]
        ctx['scheduled_tasks'] = scheduled_tasks

        ctx['previews'] = getattr(self, 'previews', [])
        ctx['employee_preview'] = getattr(self, 'employee_preview', '')
        ctx['company_preview'] = getattr(self, 'company_preview', '')
        return ctx

    def post(self, request, *args, **kwargs):
        if 'cancel' in request.POST:
            return self.cancel_task(request)
        if 'retry_log_id' in request.POST:
            from .tasks import retry_failed_sms
            retry_failed_sms.delay(request.POST.get('retry_log_id'))
            messages.success(request, "Retry initiated.")
            return redirect(self.success_url)
        form = self.get_form()
        if not form.is_valid():
            messages.error(request, "Please fix the errors.")
            return self.get(request)
        if 'preview' in request.POST:
            self.handle_preview(form)
            return self.get(request)
        if 'send' in request.POST:
            return self.handle_send(form)
        return self.get(request)

    def cancel_task(self, request):
        task_id = request.POST.get('log_id')
        task = ScheduledTask.objects.filter(task_id=task_id).first()
        if task and task.is_cancelable():
            from celery import current_app
            current_app.control.revoke(task.task_id, terminate=True)
            task.status = 'cancelled'
            task.save(update_fields=['status'])
            messages.success(request, "Broadcast cancelled.")
        return redirect(self.success_url)

    def handle_preview(self, form):
        tpl = form.cleaned_data['message']
        dt = form.cleaned_data.get('scheduled_date')
        clients = Client.objects.all()[:5]
        self.previews = []
        for client in clients:
            from .utils import personalize
            msg = personalize(tpl, client)
            self.previews.append({
                'client': f"{client.first_name or ''} {client.last_name or ''}".strip(),
                'message': msg, 'send_at': dt.strftime('%Y-%m-%d %H:%M') if dt else 'Now'
            })
        self.employee_preview = clean_placeholders(tpl)
        self.company_preview = clean_placeholders(tpl)

    def handle_send(self, form):
        tpl = form.cleaned_data['message']
        dt = form.cleaned_data.get('scheduled_date')
        scheduled_iso = dt.isoformat() if dt else None
        try:
            schedule_bulk_broadcast.delay(tpl, scheduled_iso)
            messages.success(self.request, f"Broadcast queued.")
            return redirect(self.success_url)
        except Exception as exc:
            logger.exception("Broadcast queue failed")
            messages.error(self.request, str(exc))
            return self.get(self.request)