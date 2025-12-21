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
        payload = {"messageId": message_id}
        try:
            resp = requests.post(self.BASE_URL_DLR, headers=self.headers, json=payload, timeout=30)
            return resp.json()
        except Exception as exc:
            self.logger.exception("DLR check failed for message_id=%s", message_id)
            return {"status": False, "message": str(exc)}

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


def update_pending_sms_logs_and_balance(
    grace_seconds: int = 15,
    max_age_days_before_forced_fail: int = 7
):
    """
    Production-ready: update pending MessageLog.delivery_status by querying DLR by message_id.
    - Only checks pending messages older than grace_seconds to avoid race conditions.
    - Does not mark 'no messages found' as immediate failure (keeps pending) unless the log
      is older than max_age_days_before_forced_fail.
    - Returns a summary dict for monitoring and logging.
    """
    api = MobileSasaAPI()
    updated_count = 0
    failed_count = 0
    total_pending = 0
    now = timezone.now()
    grace_delta = timezone.timedelta(seconds=grace_seconds)
    forced_fail_delta = timezone.timedelta(days=max_age_days_before_forced_fail)

    try:
        pending_qs = MessageLog.objects.filter(delivery_status='pending')
        total_pending = pending_qs.count()
        logger.debug("Found %s pending message logs", total_pending)

        # iterate in small batches to avoid long DB locks
        for log in pending_qs.order_by('timestamp').iterator():
            # skip if still too fresh
            if (now - log.timestamp) < grace_delta:
                logger.debug("Skipping recent message (id=%s) younger than grace period", getattr(log, 'id', None))
                continue

            # if message_id missing — keep as pending but tag error_details for review
            if not log.message_id:
                # if older than forced_fail_delta, mark failed; otherwise keep pending
                if (now - log.timestamp) > forced_fail_delta:
                    log.send_status = 'failed'
                    log.delivery_status = 'failed'
                    log.error_details = (log.error_details or '') + " | Missing message_id and too old -> forced fail"
                    log.save(update_fields=['delivery_status', 'send_status', 'error_details'])
                    failed_count += 1
                    logger.warning("Forcing fail for message without message_id (id=%s)", getattr(log, 'id', None))
                else:
                    logger.info("Leave pending (no message_id yet) for log id=%s", getattr(log, 'id', None))
                continue

            # Call provider DLR endpoint
            try:
                result = api.check_delivery_status(log.message_id)
                logger.debug("DLR result for message_id=%s: %s", log.message_id, result)
            except Exception as exc:
                # leave pending but log exception
                log.error_details = (log.error_details or '') + f" | DLR call exception: {str(exc)}"
                log.save(update_fields=['error_details'])
                failed_count += 0
                logger.exception("DLR API call exception for message_id=%s", log.message_id)
                continue

            # If provider returned status False with "No messages found", DON'T mark as failed —
            # might be a timing/propagation issue. Only mark failed for explicit failure statuses.
            if not isinstance(result, dict):
                # unexpected shape - keep pending but log
                log.error_details = (log.error_details or '') + f" | Unexpected DLR response: {repr(result)}"
                log.save(update_fields=['error_details'])
                logger.warning("Unexpected DLR return shape for message_id=%s", log.message_id)
                continue

            # Provider says ok and has messages array
            if result.get('status') and result.get('messages'):
                # use the first message record for status
                msg_info = result['messages'][0]
                api_status_raw = None
                # provider may nest deliveryStatus or return top-level keys
                if isinstance(msg_info.get('deliveryStatus'), dict):
                    api_status_raw = msg_info.get('deliveryStatus', {}).get('status')
                else:
                    api_status_raw = msg_info.get('deliveryStatus') or msg_info.get('status') or None

                normalized = _normalize_api_delivery_status(api_status_raw)
                previous_delivery = log.delivery_status
                previous_send = log.send_status

                if normalized == 'delivered':
                    log.delivery_status = 'delivered'
                    log.send_status = 'sent'
                elif normalized == 'pending':
                    # provider indicates still in transit / accepted -> keep 'pending' but ensure send_status is 'sent'
                    log.delivery_status = 'pending'
                    log.send_status = 'sent'
                else:  # 'failed'
                    log.delivery_status = 'failed'
                    log.send_status = 'failed'

                # attach raw provider snippet for auditing
                log.error_details = (log.error_details or '') + f" | DLR raw: {msg_info}"
                log.save(update_fields=['delivery_status', 'send_status', 'error_details'])
                updated_count += 1
                logger.info("Updated log id=%s: delivery %s (was %s)", getattr(log, 'id', None), log.delivery_status, previous_delivery)

            else:
                # provider returned status False or empty messages
                # If it's a genuine "no messages found", don't fail immediately
                response_message = result.get('message') or result.get('responseCode') or ''
                logger.info("DLR returned no messages for message_id=%s -> message: %s", log.message_id, response_message)

                # If the log is very old, mark failed (stale)
                if (now - log.timestamp) > forced_fail_delta:
                    log.delivery_status = 'failed'
                    log.send_status = 'failed'
                    log.error_details = (log.error_details or '') + f" | DLR no messages & too old: {result}"
                    log.save(update_fields=['delivery_status', 'send_status', 'error_details'])
                    failed_count += 1
                    logger.warning("Forcing fail for old log id=%s due to missing DLR entry", getattr(log, 'id', None))
                else:
                    # keep pending
                    log.error_details = (log.error_details or '') + f" | DLR no messages: {result}"
                    log.save(update_fields=['error_details'])
                    logger.debug("Keeping log id=%s pending after DLR no-message response", getattr(log, 'id', None))
                    # don't increment failed_count

    except Exception as exc:
        logger.exception("Failed while updating pending SMS logs: %s", exc)

    # Fetch current balance (best effort)
    balance_info = {}
    try:
        balance_info = api.get_balance()
        logger.info("Current SMS balance: %s", balance_info)
    except Exception as exc:
        logger.exception("Failed to fetch SMS balance: %s", exc)

    summary = {
        "total_pending": total_pending,
        "updated_count": updated_count,
        "failed_count": failed_count,
        "balance": balance_info
    }
    return summary