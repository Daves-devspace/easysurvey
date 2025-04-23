import logging

import requests
from django.core.cache import cache



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
    BASE_URL_BULK_PERSONALIZED = "https://api.mobilesasa.com/v1/send/bulk-personalized"

    def __init__(self):
        token_data = get_sms_provider_token()
        if token_data:
            self.api_key = token_data.get('api_token')
            self.sender_id = token_data.get('sender_id')
        else:
            raise ValueError("No API token or sender ID found. Please check the token setup.")

        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self.logger = logging.getLogger(__name__)


    def clean_phone_number(self, phone):
        """Standardizes phone numbers to match API requirements."""
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
        cleaned_phone = self.clean_phone_number(phone_number)
        if not cleaned_phone:
            return {"status": False, "message": "Invalid phone number"}
        payload = {
            "senderID": self.sender_id,
            "message": message,
            "phone": cleaned_phone,
        }
        response = requests.post(self.BASE_URL_SINGLE, headers=self.headers, json=payload)
        return response.json()

    def check_delivery_status(self, message_id):
        url = f"https://api.mobilesasa.com/v1/check_status/{message_id}"
        response = requests.get(url, headers=self.headers)
        return response.json()

    def get_balance(self):
        url = "https://api.mobilesasa.com/v1/balance"
        response = requests.get(url, headers=self.headers)
        return response.json()

    def send_bulk_sms(self, message, phone_numbers):
        chunk_size = 50
        success_count = 0
        errors = []
        cleaned_numbers = [self.clean_phone_number(phone) for phone in phone_numbers if phone]

        for i in range(0, len(cleaned_numbers), chunk_size):
            chunk = cleaned_numbers[i:i + chunk_size]
            phones = ",".join(chunk)
            payload = {
                "senderID": self.sender_id,
                "message": message,
                "phones": phones,
            }
            try:
                response = requests.post(self.BASE_URL_BULK, headers=self.headers, json=payload)
                response.raise_for_status()
                response_data = response.json()
                if response_data.get("status"):
                    success_count += len(chunk)
                else:
                    errors.append({"message": response_data.get("message", "Unknown error"), "phones": chunk})
            except requests.RequestException as e:
                errors.append({"message": str(e), "phones": chunk})
        return success_count, errors








def load_email_settings():
    try:
        s = EmailSettings.objects.get()
        settings.EMAIL_HOST = s.email_host or settings.EMAIL_HOST
        settings.EMAIL_PORT = s.email_port or settings.EMAIL_PORT
        settings.EMAIL_HOST_USER = s.email_host_user or settings.EMAIL_HOST_USER
        settings.EMAIL_HOST_PASSWORD = s.email_host_password or settings.EMAIL_HOST_PASSWORD
        settings.DEFAULT_FROM_EMAIL = s.default_from_email or settings.DEFAULT_FROM_EMAIL
    except EmailSettings.DoesNotExist:
        pass