from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView
from .models import MessageLog
from .utils import MobileSasaAPI


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




class MessageLogListView(LoginRequiredMixin, ListView):
    model = MessageLog
    template_name = 'Management/comunication.html'
    context_object_name = 'logs'
    paginate_by = 50
    ordering = ['-timestamp']