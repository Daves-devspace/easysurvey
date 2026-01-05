# apps/EasyDocs/communication.py
from datetime import datetime
import logging
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import ListView
from django.views.generic.edit import FormMixin
from django.db.models import Count, Q
from django.core.cache import cache
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

    CACHE_TIMEOUT = 60 * 5        # 5 min
    CACHE_COUNTS_TIMEOUT = 60 * 2 # 2 min (delivery changes often)

    # --------------------------------------------------
    # Queryset
    # --------------------------------------------------
    def get_queryset(self):
        return MessageLog.objects.select_related('client').order_by('-timestamp')

    # --------------------------------------------------
    # Context
    # --------------------------------------------------
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['form'] = self.get_form()

        # -------- Cached counts --------
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

        # -------- Cached failed logs preview --------
        failed_cache_key = 'messagelog_failed_preview'
        failed_logs = cache.get(failed_cache_key)

        if not failed_logs:
            qs = MessageLog.objects.filter(send_status='failed').select_related('client').order_by('-timestamp')[:50]
            failed_logs = []
            for log in qs:
                client_name = f"{log.client.first_name or ''} {log.client.last_name or ''}".strip() if log.client else None
                failed_logs.append({
                    'id': log.id,
                    'message': log.message,
                    'phone': log.phone,
                    'timestamp': log.timestamp,
                    'error_details': log.error_details or "Unknown error",
                    'client_name': client_name
                })
            cache.set(failed_cache_key, failed_logs, self.CACHE_TIMEOUT)

        ctx['failed_logs'] = failed_logs

        # -------- Cached scheduled tasks --------
        scheduled_cache_key = 'scheduled_tasks_pending'
        scheduled_tasks = cache.get(scheduled_cache_key)
        if not scheduled_tasks:
            scheduled_tasks = list(
                ScheduledTask.objects.filter(
                    status='pending',
                    scheduled_time__gt=timezone.now()
                )
                .order_by('scheduled_time')
                .values('task_id', 'scheduled_time', 'status', 'completed_at','task_name','message_preview')[:50]
            )
            cache.set(scheduled_cache_key, scheduled_tasks, self.CACHE_TIMEOUT)

        ctx['scheduled_tasks'] = scheduled_tasks

        # -------- Previews --------
        ctx['previews'] = getattr(self, 'previews', [])
        ctx['employee_preview'] = getattr(self, 'employee_preview', '')
        ctx['company_preview'] = getattr(self, 'company_preview', '')

        return ctx

    # --------------------------------------------------
    # POST handlers
    # --------------------------------------------------
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

    # --------------------------------------------------
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

    # --------------------------------------------------
    def handle_preview(self, form):
        tpl = form.cleaned_data['message']
        dt = form.cleaned_data.get('scheduled_date')

        clients = Client.objects.all()[:5]
        self.previews = []

        for client in clients:
            send_at = dt.strftime('%Y-%m-%d %H:%M') if dt else 'Now'
            msg = tpl.replace('{client_first_name}', client.first_name or '')
            msg = msg.replace('{client_last_name}', client.last_name or '')
            self.previews.append({
                'client': f"{client.first_name or ''} {client.last_name or ''}".strip(),
                'message': msg,
                'send_at': send_at
            })

        self.employee_preview = clean_placeholders(tpl)
        self.company_preview = clean_placeholders(tpl)

    # --------------------------------------------------
    def handle_send(self, form):
        tpl = form.cleaned_data['message']
        dt = form.cleaned_data.get('scheduled_date')
        scheduled_iso = dt.isoformat() if dt else None

        try:
            res = schedule_bulk_broadcast.delay(tpl, scheduled_iso)
            messages.success(self.request, f"Broadcast queued. Task ID: {res.id}")
            return redirect(self.success_url)
        except Exception as exc:
            logger.exception("Broadcast queue failed")
            messages.error(self.request, str(exc))
            return self.get(self.request)