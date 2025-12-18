import logging
import requests
from django.core.cache import cache
from apps.tenant_management.models import NotificationLog, Tenant

logger = logging.getLogger(__name__)

def get_sms_provider_token():
    """
    Fetches SMS token from cache or EasyDocs model.
    """
    token = cache.get('sms_provider_token')
    if token is None:
        try:
            # Assuming EasyDocs is installed and has the token
            from apps.EasyDocs.models import SmsProviderToken
            token_obj = SmsProviderToken.objects.first()
            if token_obj:
                token = {
                    "api_token": token_obj.api_token,
                    "sender_id": token_obj.sender_id,
                }
                cache.set('sms_provider_token', token, timeout=3600)
        except ImportError:
            logger.warning("EasyDocs app not found. SMS token cannot be retrieved.")
            return None
    return token

class MobileSasaAPI:
    BASE_URL_SINGLE = "https://api.mobilesasa.com/v1/send/message"
    BASE_URL_BULK = "https://api.mobilesasa.com/v1/send/bulk"
    BASE_URL_PERSONALIZED = "https://api.mobilesasa.com/v1/send/bulk-personalized"
    BASE_URL_BALANCE = "https://api.mobilesasa.com/v1/get-balance"

    def __init__(self):
        token_data = get_sms_provider_token()
        if not token_data:
            # Fallback or raise error depending on preference. 
            # Raising error ensures we know config is missing.
            raise ValueError("No SMS API token found. Check SmsProviderToken configuration.")
            
        self.api_key = token_data.get('api_token')
        self.sender_id = token_data.get('sender_id')
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def clean_phone_number(self, phone):
        if not phone: return None
        phone = ''.join(filter(str.isdigit, str(phone)))
        if phone.startswith('0'): phone = '254' + phone[1:]
        elif phone.startswith('+'): phone = phone[1:]
        elif len(phone) == 9: phone = '254' + phone
        return phone

    def send_single_sms(self, tenant, message):
        """
        Sends a single SMS to a Tenant and logs it.
        """
        phone = self.clean_phone_number(tenant.phone_number)
        if not phone:
            logger.error(f"Invalid phone for tenant {tenant.id}")
            return False

        payload = {"senderID": self.sender_id, "message": message, "phone": phone}
        
        status = 'failed'
        try:
            resp = requests.post(self.BASE_URL_SINGLE, headers=self.headers, json=payload)
            data = resp.json()
            if data.get('status'):
                status = 'sent'
        except Exception as e:
            logger.error(f"SMS Exception: {e}")

        # Log to NotificationLog
        NotificationLog.objects.create(
            tenant=tenant,
            message=message,
            channel=NotificationLog.SMS,
            status=status
        )
        return status == 'sent'

    def send_bulk_sms(self, message, tenants):
        """
        Sends the SAME message to a list of Tenants (e.g. Announcement).
        """
        chunk_size = 50
        success_count = 0
        
        # Prepare valid list
        valid_tenants = []
        for t in tenants:
            p = self.clean_phone_number(t.phone_number)
            if p: valid_tenants.append((t, p))

        for i in range(0, len(valid_tenants), chunk_size):
            chunk = valid_tenants[i:i+chunk_size] # List of (tenant, phone) tuples
            phones_str = ",".join([pair[1] for pair in chunk])
            
            payload = {"senderID": self.sender_id, "message": message, "phones": phones_str}
            
            current_status = 'failed'
            try:
                r = requests.post(self.BASE_URL_BULK, headers=self.headers, json=payload)
                if r.json().get('status'):
                    current_status = 'sent'
                    success_count += len(chunk)
            except Exception as e:
                logger.error(f"Bulk SMS Error: {e}")

            # Log for each tenant
            for t, p in chunk:
                NotificationLog.objects.create(
                    tenant=t,
                    message=message,
                    channel=NotificationLog.SMS,
                    status=current_status
                )
                
        return success_count

    def send_personalized_bulk(self, messages_data):
        """
        Sends DIFFERENT messages to different numbers in one API call.
        messages_data: list of dicts [{'tenant': TenantObj, 'message': '...'}, ...]
        """
        chunk_size = 50
        success_count = 0
        
        for i in range(0, len(messages_data), chunk_size):
            chunk = messages_data[i:i+chunk_size]
            body = []
            
            for item in chunk:
                phone = self.clean_phone_number(item['tenant'].phone_number)
                if phone:
                    body.append({'phone': phone, 'message': item['message']})
            
            if not body: continue

            payload = {"senderID": self.sender_id, "messageBody": body}
            current_status = 'failed'
            
            try:
                r = requests.post(self.BASE_URL_PERSONALIZED, headers=self.headers, json=payload)
                if r.json().get('status'):
                    current_status = 'sent'
                    success_count += len(body)
            except Exception as e:
                logger.error(f"Personalized SMS Error: {e}")

            # Log
            for item in chunk:
                NotificationLog.objects.create(
                    tenant=item['tenant'],
                    message=item['message'],
                    channel=NotificationLog.SMS,
                    status=current_status
                )

        return success_count