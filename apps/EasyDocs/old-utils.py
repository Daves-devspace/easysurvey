import logging
from datetime import datetime
import re
import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db.utils import OperationalError, ProgrammingError
from django.db import models
from apps.EasyDocs.models import MessageLog, Client, SmsProviderToken, SiteSettings
from django.db import transaction
from datetime import timedelta
from apps.EasyDocs.models import MessageLog, Client
import logging
logger = logging.getLogger(__name__)    

def get_sms_provider_token():
    # Import inside the function to avoid circular import
    from apps.EasyDocs.models import SmsProviderToken

    # Attempt to fetch the token from the cache
    token = cache.get('sms_provider_token')

    if token is None:
        # Fetch the most recent SmsProviderToken object
        token_obj = SmsProviderToken.objects.first()  # You can adjust this to get the latest record if needed
        if token_obj:
            # Return both the api_token and sender_id in the dictionary
            token = {
                "api_token": token_obj.api_token,
                "sender_id": token_obj.sender_id,
            }
            # Save the token in the cache for 1 hour
            cache.set('sms_provider_token', token, timeout=3600)
        else:
            # If no token object is found, return None
            token = None

    return token


class MobileSasaAPI:
    BASE_URL_SINGLE = "https://api.mobilesasa.com/v1/send/message"
    BASE_URL_BULK = "https://api.mobilesasa.com/v1/send/bulk"
    BASE_URL_PERSONALIZED = "https://api.mobilesasa.com/v1/send/bulk-personalized"
    BASE_URL_BALANCE = "https://api.mobilesasa.com/v1/get-balance"
    BASE_URL_STATUS = "https://api.mobilesasa.com/v1/check_status/{message_id}"
    BASE_URL_DLR = "https://api.mobilesasa.com/v1/dlr"

    def __init__(self):
        token_data = get_sms_provider_token()
        if not token_data:
            raise ValueError("No API token or sender ID found. Please check the token setup.")
        self.api_key = token_data.get('api_token')
        self.sender_id = token_data.get('sender_id')
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self.logger = logging.getLogger(__name__)

    def clean_phone_number(self, phone):
        if not phone:
            return None
        phone = ''.join(filter(str.isdigit, str(phone)))
        if phone.startswith('0'):
            phone = '254' + phone[1:]
        elif phone.startswith('+'):
            phone = phone[1:]
        elif len(phone) == 9:
            phone = '254' + phone
        return phone

    def send_sms(self, phone_number, message):
        cleaned = self.clean_phone_number(phone_number)
        if not cleaned:
            return {"status": False, "message": "Invalid phone number"}
        payload = {"senderID": self.sender_id, "message": message, "phone": cleaned}
        resp = requests.post(self.BASE_URL_SINGLE, headers=self.headers, json=payload)
        return resp.json()

    def send_bulk_sms(self, message, phone_numbers):
        chunk_size = 50
        success_count = 0
        errors = []
        cleaned = [self.clean_phone_number(p) for p in phone_numbers if p]
        for i in range(0, len(cleaned), chunk_size):
            chunk = cleaned[i:i+chunk_size]
            payload = {"senderID": self.sender_id, "message": message, "phones": ",".join(chunk)}
            try:
                r = requests.post(self.BASE_URL_BULK, headers=self.headers, json=payload)
                r.raise_for_status()
                data = r.json()
                if data.get('status'):
                    for p in chunk:
                        client = Client.objects.filter(phone=p).first()
                        MessageLog.objects.create(
                            client=client,
                            phone=p,
                            message=message,
                            reason="Bulk SMS",
                            send_status='sent',
                            delivery_status='pending'
                        )
                    success_count += len(chunk)
                else:
                    errors.append({'message': data.get('message'), 'phones': chunk})
            except Exception as e:
                errors.append({'message': str(e), 'phones': chunk})
        return success_count, errors


    # -----------------------------
    # Bulletproof send_personalized_sms
    # -----------------------------
    def send_personalized_sms(self, message_pairs):
        """
        Fully resilient personalized SMS sender.

        Always returns:
            {"raw": ..., "sent": [...], "failed": [...]}
        """
        # Clean and prepare payload
        chunk = []
        for item in message_pairs:
            phone = self.clean_phone_number(item.get('phone')) if isinstance(item, dict) else None
            message = item.get('message') if isinstance(item, dict) else None
            if phone and message:
                chunk.append({"phone": phone, "message": message})

        if not chunk:
            return {"raw": None, "sent": [], "failed": []}

        payload = {"senderID": self.sender_id, "messageBody": chunk}

        try:
            resp = requests.post(self.BASE_URL_PERSONALIZED, headers=self.headers, json=payload, timeout=40)
            try:
                raw = resp.json()
            except Exception:
                raw = resp.text or str(resp)
        except Exception as exc:
            raw = str(exc)
            return {"raw": raw,
                    "sent": [],
                    "failed": [{"phone": it.get('phone'), "error": str(exc), "raw": None} for it in chunk]}

        sent = []
        failed = []

        def normalize_item(item):
            if isinstance(item, dict):
                phone = item.get('phone') or item.get('msisdn')
                message_id = item.get('message_id') or item.get('messageId') or item.get('id')
                return {"phone": phone, "message_id": message_id, "raw": item}
            elif isinstance(item, str):
                return {"phone": item, "message_id": None, "raw": item}
            else:
                return {"phone": None, "message_id": None, "raw": item}

        try:
            if isinstance(raw, dict):
                for msg in raw.get('messages') or raw.get('sent') or []:
                    ni = normalize_item(msg)
                    if ni["phone"]:
                        sent.append(ni)
                for f in raw.get('failed', []):
                    ni = normalize_item(f)
                    error = f.get('error') if isinstance(f, dict) else str(f)
                    ni["error"] = error or "provider_error"
                    if ni["phone"]:
                        failed.append(ni)
            elif isinstance(raw, list):
                for item in raw:
                    ni = normalize_item(item)
                    if ni["phone"]:
                        sent.append(ni)
            elif isinstance(raw, str):
                for it in chunk:
                    failed.append({"phone": it.get('phone'), "error": raw, "raw": raw})
            else:
                for it in chunk:
                    failed.append({"phone": it.get('phone'), "error": "unexpected_provider_response", "raw": raw})
        except Exception as exc:
            for it in chunk:
                failed.append({"phone": it.get('phone'), "error": f"normalization_exception: {exc}", "raw": raw})

        # Ensure all phones accounted for
        sent_phones = {s['phone'] for s in sent if s.get('phone')}
        failed_phones = {f['phone'] for f in failed if f.get('phone')}
        for it in chunk:
            phone = it.get('phone')
            if phone not in sent_phones and phone not in failed_phones:
                failed.append({"phone": phone, "error": "unaccounted_message", "raw": raw})

        return {"raw": raw, "sent": sent, "failed": failed}
        
        
    
    
    def check_delivery_status(self, message_id):
            """
            Safely check delivery status for a single message.
            Returns:
                dict: {status: bool, messages: [...]} or error info
            """
            if not message_id:
                # Handle None, empty string, or invalid IDs gracefully
                self.logger.warning("check_delivery_status called with empty message_id")
                return {"status": False, "messages": [], "error": "empty_message_id"}

            payload = {"messageId": message_id}
            try:
                resp = requests.post(self.BASE_URL_DLR, headers=self.headers, json=payload, timeout=30)
                try:
                    data = resp.json()
                except Exception:
                    data = {"status": False, "messages": [], "raw_text": resp.text or str(resp)}
                return data
            except Exception as exc:
                self.logger.exception("DLR check failed for message_id=%s", message_id)
                return {"status": False, "messages": [], "error": str(exc)}
            
    def get_balance(self):
        
        try:
            resp = requests.get(self.BASE_URL_BALANCE, headers=self.headers, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            return data
        except Exception as e:
            self.logger.exception("Error fetching balance: %s", e)
            return {'status': False, 'balance': 0, 'error': str(e)}

# def personalize(template: str, client) -> str:
#     return (
#         template
#         .replace("{client_first_name}", client.first_name or "")
#         .replace("{client_last_name}", client.last_name or "")
#     )
def personalize(template: str, client) -> str:
    if not template:
        return ""
    return (
        template
        .replace("{client_first_name}", client.first_name or "")
        .replace("{client_last_name}", client.last_name or "")
    )


def clean_placeholders(template: str) -> str:
    """
    Remove any {placeholder} tokens and collapse whitespace.
    """
    if not template:
        return ""
    cleaned = re.sub(r'\{[^}]+\}', '', template)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def send_company_copy_if_needed(message: str, reason='Bulk SMS broadcast'):
    """
    Sends a single cleaned company copy (if configured and not already sent).
    """
    settings = SiteSettings.objects.first()
    if not settings or not settings.company_phone:
        logger.warning("No company phone configured; skipping company copy.")
        return None

    already_sent = MessageLog.objects.filter(is_company_copy=True, reason=reason).exists()
    if already_sent:
        return None

    api = MobileSasaAPI()
    cleaned = clean_placeholders(message)
    try:
        resp = api.send_sms(settings.company_phone, cleaned)
        status = 'sent' if resp.get('status') else 'failed'
        message_id = resp.get('message_id') if isinstance(resp, dict) else None
    except Exception as e:
        logger.exception("Failed to send company copy: %s", e)
        status = 'failed'
        message_id = None

    MessageLog.objects.create(
        client=None,
        phone=settings.company_phone,
        message=cleaned,
        reason=reason,
        recipient_type='company',
        message_id=message_id,
        is_company_copy=True,
        send_status=status,
        delivery_status='pending' if status == 'sent' else 'failed',
        error_details=None if status == 'sent' else 'Failed to send company copy'
    )
    return True



def send_single_sms(client, message, reason=""):
    """
    1) Instantiates the API wrapper
    2) Sends the SMS
    3) Logs to MessageLog
    4) Returns (status, raw_response)
    """
    sms_api = MobileSasaAPI()
    resp    = sms_api.send_sms(client.phone, message)

    status   = 'sent'   if resp.get('status') else 'failed'
    delivery = 'pending' if status == 'sent' else 'failed'
    error    = None      if status == 'sent' else resp.get('message')

    MessageLog.objects.create(
        client       = client,
        phone        = client.phone,
        message      = message,
        reason       = reason,
        send_status  = status,
        delivery_status = delivery,
        error_details   = error
    )

    return status, resp

import logging
log = logging.getLogger(__name__)



# -------------------------
# Delivery update function
# -------------------------
def _normalize_api_delivery_status(api_status_raw: str) -> str:
    """
    Normalize provider delivery status text into our 'pending'|'delivered'|'failed'.
    """
    if not api_status_raw:
        return 'pending'
    s = api_status_raw.strip().lower()
    # success/delivered
    if s in ('delivrd', 'delivered', 'deliveredtoterminal', 'deliveredtoterminal'.lower()):
        return 'delivered'
    # accepted/queued/in-transit
    if s in ('accepted', 'enrouted', 'sent', 'enroute', 'enroute'): 
        return 'pending'
    # explicit failures
    if s in (
        'undeliv', 'undelevrd', 'deleted', 'undeliverable', 'rejected',
        'absentsubscriber', 'absentSubscriber'.lower(), 'deliveryimpossible',
        'network failure', 'blacklisted', 'expired', 'undelivered', 'undelivered'.lower()
    ):
        return 'failed'
    # fallback: don't assume failure
    return 'pending'


# Provider delivery state normalization
DELIVERED_STATES = {'delivered', 'success'}
FAILED_STATES = {'failed', 'undelivered', 'rejected', 'expired'}

GRACE_PERIOD_SECONDS = 30
FORCED_FAIL_HOURS = 24

def update_pending_sms_logs_and_balance():
    """
    Poll provider delivery reports (DLR) and update pending MessageLog rows.
    Fully resilient against missing message_id and API failures.
    """

    summary = {
        'checked': 0,
        'delivered': 0,
        'failed': 0,
        'still_pending': 0,
        'forced_failed': 0,
        'skipped_missing_message_id': 0,
        'errors': 0,
        'balance': None,
    }

    now = timezone.now()
    grace_delta = timedelta(seconds=GRACE_PERIOD_SECONDS)
    forced_fail_delta = timedelta(hours=FORCED_FAIL_HOURS)

    api = MobileSasaAPI()  # single instance for the loop

    with transaction.atomic():
        pending_logs = (
            MessageLog.objects
            .select_for_update(skip_locked=True)
            .filter(delivery_status='pending')
            .only('id', 'message_id', 'timestamp', 'delivery_status', 'send_status', 'error_details')
        )

        for log in pending_logs:
            summary['checked'] += 1

            if not log.message_id:
                summary['skipped_missing_message_id'] += 1
                continue

            if (now - log.timestamp) < grace_delta:
                summary['still_pending'] += 1
                continue

            if (now - log.timestamp) > forced_fail_delta:
                log.delivery_status = 'failed'
                log.send_status = 'failed'
                log.error_details = 'DLR timeout after 24h'
                log.save(update_fields=['delivery_status', 'send_status', 'error_details'])
                summary['forced_failed'] += 1
                continue

            try:
                response = api.check_delivery_status(log.message_id)
                messages = response.get('messages') if isinstance(response, dict) else []
                if not messages:
                    summary['still_pending'] += 1
                    continue

                msg_info = messages[0]
                raw_status = msg_info.get('deliveryStatus') or msg_info.get('status') or msg_info.get('state') or ''
                normalized = str(raw_status).lower()

                if normalized in DELIVERED_STATES:
                    log.delivery_status = 'delivered'
                    log.send_status = 'sent'
                    log.error_details = None
                    summary['delivered'] += 1
                elif normalized in FAILED_STATES:
                    log.delivery_status = 'failed'
                    log.send_status = 'failed'
                    log.error_details = raw_status or 'Provider failure'
                    summary['failed'] += 1
                else:
                    summary['still_pending'] += 1

                log.save(update_fields=['delivery_status', 'send_status', 'error_details'])

            except Exception as exc:
                summary['errors'] += 1
                logger.exception(f"DLR update failed for log id={log.id} message_id={log.message_id}")

    # -----------------------------
    # Fetch balance safely
    # -----------------------------
    try:
        balance_data = api.get_balance()
        summary['balance'] = balance_data.get('balance') if isinstance(balance_data, dict) else None
        logger.info(f"✅ SMS balance fetched: {summary['balance']}")
    except Exception as exc:
        summary['balance'] = None
        logger.exception(f"Failed to fetch SMS balance: {exc}")

    # -----------------------------
    # Log final summary safely
    # -----------------------------
    logger.info(
        f"SMS DLR update summary: checked={summary['checked']} delivered={summary['delivered']} "
        f"failed={summary['failed']} forced_failed={summary['forced_failed']} "
        f"pending={summary['still_pending']} skipped_missing_message_id={summary['skipped_missing_message_id']} "
        f"errors={summary['errors']} balance={summary['balance']}"
    )

    return summary
