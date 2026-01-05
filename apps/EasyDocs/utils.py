import logging
from datetime import datetime, timedelta
import re
import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.db import transaction
from apps.EasyDocs.models import MessageLog, Client, SmsProviderToken, SiteSettings

logger = logging.getLogger(__name__)

# Constants for DLR normalization
DELIVERED_STATES = {'delivered', 'success', 'delivrd', 'deliveredtoterminal'}
FAILED_STATES = {
    'failed', 'undelivered', 'rejected', 'expired', 
    'undeliv', 'undelevrd', 'deleted', 'undeliverable',
    'absentsubscriber', 'deliveryimpossible', 'network failure', 'blacklisted'
}

def get_sms_provider_token():
    from apps.EasyDocs.models import SmsProviderToken
    token = cache.get('sms_provider_token')
    if token is None:
        token_obj = SmsProviderToken.objects.first()
        if token_obj:
            token = {
                "api_token": token_obj.api_token,
                "sender_id": token_obj.sender_id,
            }
            cache.set('sms_provider_token', token, timeout=3600)
        else:
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
            # Prevent crashing if token is missing, just log error
            self.api_key = None
            self.sender_id = None
            logger.error("No API token or sender ID found.")
            # We don't raise error here to allow robust handling in caller
        else:
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
        """
        Sends a single SMS.
        """
        if not self.api_key:
            return {"status": False, "message": "Missing Configuration"}

        # 1. Validation for empty message (Fixes 0422 Error)
        if not message or not str(message).strip():
            return {"status": False, "message": "Empty message payload (blocked internally)"}

        cleaned = self.clean_phone_number(phone_number)
        if not cleaned:
            return {"status": False, "message": "Invalid phone number"}
        
        payload = {"senderID": self.sender_id, "message": message, "phone": cleaned}
        try:
            resp = requests.post(self.BASE_URL_SINGLE, headers=self.headers, json=payload, timeout=20)
            return resp.json()
        except Exception as e:
            return {"status": False, "message": str(e)}

    def send_bulk_sms(self, message, phone_numbers):
        """
        Used mostly by simple bulk forms.
        """
        if not message or not str(message).strip():
            return 0, [{"message": "Empty message payload", "phones": phone_numbers}]

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
                    # Create logs for successful submission
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

    def send_personalized_sms(self, message_pairs):
        """
        Fully resilient personalized SMS sender.
        """
        if not self.api_key:
             return {"raw": "Missing Config", "sent": [], "failed": []}

        chunk = []
        for item in message_pairs:
            phone = self.clean_phone_number(item.get('phone')) if isinstance(item, dict) else None
            message = item.get('message') if isinstance(item, dict) else None
            # FIX: Skip empty messages here too
            if phone and message and str(message).strip():
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
            return {
                "raw": raw,
                "sent": [],
                "failed": [{"phone": it.get('phone'), "error": str(exc), "raw": None} for it in chunk]
            }

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
        if not message_id:
            return {"status": False, "messages": [], "error": "empty_message_id"}
        if not self.api_key:
             return {"status": False, "messages": [], "error": "missing_config"}

        payload = {"messageId": message_id}
        try:
            resp = requests.post(self.BASE_URL_DLR, headers=self.headers, json=payload, timeout=30)
            try:
                data = resp.json()
            except Exception:
                data = {"status": False, "messages": [], "raw_text": resp.text or str(resp)}
            return data
        except Exception as exc:
            return {"status": False, "messages": [], "error": str(exc)}
            
    # def get_balance(self):
    #     if not self.api_key:
    #         return {'status': False, 'balance': 0, 'error': "missing_config"}
    #     try:
    #         resp = requests.get(self.BASE_URL_BALANCE, headers=self.headers, timeout=20)
    #         resp.raise_for_status()
    #         data = resp.json()
    #         return data
    #     except Exception as e:
    #         self.logger.exception("Error fetching balance: %s", e)
    #         return {'status': False, 'balance': 0, 'error': str(e)}
        
        
    def get_balance(self):
        """
        Fetches balance and updates the global cache for context processors.
        """
        if not self.api_key:
            return {'status': False, 'balance': 0, 'error': "missing_config"}
        try:
            resp = requests.get(self.BASE_URL_BALANCE, headers=self.headers, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            
            # --- AUTO-UPDATE CACHE HERE ---
            # This ensures 'global_sms_balance' is always fresh whenever this is called
            # by the scheduled task or manual sends.
            if isinstance(data, dict):
                bal = data.get('balance')
                if bal is not None:
                    # Cache for 24 hours (or until next update)
                    cache.set('global_sms_balance', bal, timeout=86400)
                    
            return data
        except Exception as e:
            self.logger.exception("Error fetching balance: %s", e)
            return {'status': False, 'balance': 0, 'error': str(e)}

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

def send_company_copy_if_needed(message: str):
    """
    Sends a single cleaned company copy (if configured).
    Does NOT create a DB log.
    """
    settings = SiteSettings.objects.first()
    if not settings or not settings.company_phone:
        return None 

    api = MobileSasaAPI()
    cleaned = clean_placeholders(message)
    
    if not cleaned:
        return {"status": False, "message": "Empty message after cleaning"}

    try:
        resp = api.send_sms(settings.company_phone, cleaned)
        return resp
    except Exception as e:
        logger.exception("Failed to send company copy: %s", e)
        return {"status": False, "message": str(e)}

def send_single_sms(client, message, reason=""):
    """
    Utility to send single SMS and Log it.
    """
    if not message or not message.strip():
        MessageLog.objects.create(
            client=client,
            phone=client.phone,
            message=message,
            reason=reason,
            send_status='failed',
            delivery_status='failed',
            error_details="Empty message payload"
        )
        return 'failed', {"message": "Empty message"}

    sms_api = MobileSasaAPI()
    resp = sms_api.send_sms(client.phone, message)

    status = 'sent' if resp.get('status') else 'failed'
    delivery = 'pending' if status == 'sent' else 'failed'
    error = None if status == 'sent' else resp.get('message')
    message_id = resp.get('message_id') if isinstance(resp, dict) else None

    MessageLog.objects.create(
        client=client,
        phone=client.phone,
        message=message,
        reason=reason,
        send_status=status,
        delivery_status=delivery,
        error_details=error,
        message_id=message_id
    )

    return status, resp

# -------------------------
# Delivery update function
# -------------------------
GRACE_PERIOD_SECONDS = 30
FORCED_FAIL_HOURS = 24

def update_pending_sms_logs_and_balance():
    """
    Poll provider delivery reports (DLR) and update pending MessageLog rows.
    """
    summary = {
        'checked': 0, 'delivered': 0, 'failed': 0, 
        'still_pending': 0, 'forced_failed': 0, 
        'skipped_missing_message_id': 0, 'errors': 0, 'balance': None,
    }

    now = timezone.now()
    grace_delta = timedelta(seconds=GRACE_PERIOD_SECONDS)
    forced_fail_delta = timedelta(hours=FORCED_FAIL_HOURS)

    api = MobileSasaAPI()

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
                # If it's older than 24 hours and has no message_id, assume failed.
                if (now - log.timestamp) > forced_fail_delta:
                    log.delivery_status = 'failed'
                    log.error_details = 'No Message ID & Timeout'
                    log.save(update_fields=['delivery_status', 'error_details'])
                continue

            if (now - log.timestamp) < grace_delta:
                summary['still_pending'] += 1
                continue

            # Force fail after 24 hours
            if (now - log.timestamp) > forced_fail_delta:
                log.delivery_status = 'failed'
                log.error_details = 'DLR timeout after 24h'
                log.save(update_fields=['delivery_status', 'error_details'])
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
                    log.error_details = None
                    if log.send_status != 'sent':
                        log.send_status = 'sent'
                    log.save(update_fields=['delivery_status', 'send_status', 'error_details'])
                    summary['delivered'] += 1

                elif normalized in FAILED_STATES:
                    log.delivery_status = 'failed'
                    log.error_details = raw_status or 'Provider DLR: Failed'
                    log.save(update_fields=['delivery_status', 'error_details'])
                    summary['failed'] += 1
                else:
                    summary['still_pending'] += 1

            except Exception as exc:
                summary['errors'] += 1
                logger.exception(f"DLR update failed for log id={log.id}")

    try:
        balance_data = api.get_balance()
        summary['balance'] = balance_data.get('balance') if isinstance(balance_data, dict) else None
    except Exception:
        summary['balance'] = None

    logger.info(f"SMS DLR Summary: {summary}")
    return summary