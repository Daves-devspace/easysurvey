import logging
from datetime import datetime

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db.utils import OperationalError, ProgrammingError

from apps.EasyDocs.models import MessageLog, Client


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

    def send_personalized_sms(self, message_pairs):
        results = {'sent': [], 'failed': []}
        for i in range(0, len(message_pairs), 50):
            chunk = message_pairs[i:i + 50]
            body = []
            for item in chunk:
                phone = self.clean_phone_number(item.get('phone'))
                msg_text = item.get('message')
                if phone and msg_text:
                    body.append({'phone': phone, 'message': msg_text})
            if not body:
                continue
            payload = {"senderID": self.sender_id, "messageBody": body}
            try:
                resp = requests.post(self.BASE_URL_PERSONALIZED, headers=self.headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                if data.get('status'):
                    # Instead of creating logs here, return the phone numbers that succeeded
                    results['sent'].extend([entry['phone'] for entry in body])
                else:
                    results['failed'].extend([entry['phone'] for entry in body])
            except Exception as e:
                results['failed'].extend([entry['phone'] for entry in body])
        return results

    def check_delivery_status(self, message_id):
        payload = {"messageId": message_id}
        response = requests.post(self.BASE_URL_DLR, headers=self.headers, json=payload)
        return response.json()

    def get_balance(self):
        try:
            resp = requests.get(self.BASE_URL_BALANCE, headers=self.headers)
            resp.raise_for_status()
            data = resp.json()
            if data.get('status'):
                return data
            self.logger.error(f"Balance fetch failed: {data.get('message')}")
            return {'balance': 0, 'error': data.get('message')}
        except Exception as e:
            self.logger.error(f"Error fetching balance: {e}")
            return {'balance': 0, 'error': str(e)}


def personalize(template: str, client) -> str:
    return (
        template
        .replace("{client_first_name}", client.first_name or "")
        .replace("{client_last_name}", client.last_name or "")
    )


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





def update_pending_sms_logs_and_balance():
    api = MobileSasaAPI()

    # 1️⃣ Update delivery status of pending messages
    pending_logs = MessageLog.objects.filter(delivery_status='pending')
    for log in pending_logs:
        try:
            result = api.check_delivery_status(log.message_id)
            if result.get('status') and result.get('messages'):
                delivery_status = result['messages'][0].get('deliveryStatus', {}).get('status', 'unknown')
                log.delivery_status = delivery_status
            else:
                log.delivery_status = 'unknown'
            log.save(update_fields=['delivery_status'])
        except Exception as e:
            log.error_details = f"Delivery check failed: {str(e)}"
            log.save(update_fields=['error_details'])

    # 2️⃣ Optionally: log or store current balance
    try:
        balance_info = api.get_balance()
        print(f"📩 Current SMS Balance: {balance_info}")  # Debug output
    except Exception as e:
        print(f"⚠️ Could not fetch SMS balance: {e}")












