import logging
from datetime import datetime

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db.utils import OperationalError, ProgrammingError

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import timedelta

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
    """
    Robust wrapper for MobileSasa API calls.

    Key improvements over the original:
    - Uses a requests.Session with retry/backoff for idempotent transient errors.
    - Adds a per-call timeout (configurable).
    - Handles different requests exceptions explicitly (Timeout, ConnectionError, HTTPError).
    - Returns predictable dict shapes for every public method.
    - Small in-memory cache for balance to prevent repeated balance calls during the same web request.
    - All original functions preserved (send_sms, send_bulk_sms, send_personalized_sms,
      check_delivery_status, get_balance) and retain similar interfaces.
    """

    BASE_URL_SINGLE = "https://api.mobilesasa.com/v1/send/message"
    BASE_URL_BULK = "https://api.mobilesasa.com/v1/send/bulk"
    BASE_URL_PERSONALIZED = "https://api.mobilesasa.com/v1/send/bulk-personalized"
    BASE_URL_BALANCE = "https://api.mobilesasa.com/v1/get-balance"
    BASE_URL_STATUS = "https://api.mobilesasa.com/v1/check_status/{message_id}"
    BASE_URL_DLR = "https://api.mobilesasa.com/v1/dlr"

    # default timeout (seconds) for all remote calls; can be overridden per-instance
    DEFAULT_TIMEOUT = 5
    # retries for idempotent calls
    RETRY_SETTINGS = {
        "total": 3,
        "backoff_factor": 0.5,
        "status_forcelist": (429, 500, 502, 503, 504),
        "allowed_methods": ("GET", "POST"),
    }

    def __init__(self, timeout: int | float = None, session: requests.Session | None = None):
        """
        Initialize the API wrapper.

        :param timeout: default timeout in seconds for HTTP calls (defaults to DEFAULT_TIMEOUT).
                        Keep it small (e.g., 3-10s) to avoid blocking web workers.
        :param session: optional requests.Session to use (useful for testing or if you want
                        to share a session across instances).
        """
        token_data = get_sms_provider_token()
        if not token_data:
            raise ValueError("No API token or sender ID found. Please check the token setup.")
        self.api_key = token_data.get("api_token")
        self.sender_id = token_data.get("sender_id")
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self.logger = logging.getLogger(__name__)
        self.timeout = timeout or self.DEFAULT_TIMEOUT

        # Create a session with retries to handle transient errors gracefully
        if session is None:
            s = requests.Session()
            retry = Retry(
                total=self.RETRY_SETTINGS["total"],
                backoff_factor=self.RETRY_SETTINGS["backoff_factor"],
                status_forcelist=self.RETRY_SETTINGS["status_forcelist"],
                allowed_methods=self.RETRY_SETTINGS["allowed_methods"],
                raise_on_status=False,
            )
            adapter = HTTPAdapter(max_retries=retry)
            s.mount("https://", adapter)
            s.mount("http://", adapter)
            self.session = s
        else:
            self.session = session

        # Lightweight in-memory cache for balance fetches to avoid repeated calls
        self._balance_cache = {"value": None, "fetched_at": None}
        self._balance_cache_ttl = timedelta(seconds=30)  # configurable TTL

    # ------------------------------
    # Phone cleaning / normalization
    # ------------------------------
    def clean_phone_number(self, phone):
        """
        Normalize a phone number to the E.164-like format expected by your provider:
        - Convert things like "0712345678", "+254712345678", "712345678" -> "254712345678"
        - Returns None for invalid/empty input.

        Notes:
        - The function is defensive: removes spaces, dashes, parentheses.
        - It expects Kenyan numbers primarily (country code 254). Adjust logic if you support other countries.
        """
        if not phone:
            return None
        original = str(phone).strip()

        # Keep the original to detect leading '+' (user pasted +254...)
        had_plus = original.startswith("+")

        # Remove any non-digit characters
        digits = "".join(c for c in original if c.isdigit())

        # If the user typed +254..., digits will start with 254
        if had_plus and digits.startswith("254"):
            return digits

        # If they typed 07xxxxxxxx (10 digits starting with 0)
        if digits.startswith("0") and len(digits) == 10:
            return "254" + digits[1:]

        # If they typed 7xxxxxxxx (9 digits), common shorthand
        if len(digits) == 9:
            return "254" + digits

        # If they typed full international digits (254...) and length looks right (>=12)
        if digits.startswith("254") and len(digits) >= 12:
            return digits

        # Unknown/invalid format
        return None

    # ------------------------------
    # Single SMS
    # ------------------------------
    def send_sms(self, phone_number, message):
        """
        Send a single SMS. Returns a dict with at least:
        { 'status': bool, 'message': str, 'raw': <raw provider response or text> }

        This function will not raise on request errors; it returns structured failure info.
        """
        cleaned = self.clean_phone_number(phone_number)
        if not cleaned:
            return {"status": False, "message": "Invalid phone number", "raw": None}

        payload = {"senderID": self.sender_id, "message": message, "phone": cleaned}

        try:
            r = self.session.post(self.BASE_URL_SINGLE, headers=self.headers, json=payload, timeout=self.timeout)
            # HTTP errors (4xx/5xx) won't raise because raise_on_status=False in Retry; handle explicitly:
            r.raise_for_status()
            data = r.json() if r.text else {}
            # Standardize provider response to our format:
            if data.get("status"):
                return {"status": True, "message": data.get("message", "sent"), "raw": data}
            else:
                return {"status": False, "message": data.get("message", "failed"), "raw": data}
        except requests.Timeout as e:
            self.logger.error(f"send_sms timeout for {cleaned}: {e}")
            return {"status": False, "message": "timeout", "raw": None}
        except requests.ConnectionError as e:
            self.logger.error(f"send_sms connection error for {cleaned}: {e}")
            return {"status": False, "message": "connection_error", "raw": None}
        except requests.HTTPError as e:
            # r.raise_for_status() raised
            self.logger.error(f"send_sms http error for {cleaned}: {e}")
            try:
                raw = r.json()
            except Exception:
                raw = r.text if hasattr(r, "text") else None
            return {"status": False, "message": "http_error", "raw": raw}
        except Exception as e:
            self.logger.exception(f"Unexpected error in send_sms for {cleaned}: {e}")
            return {"status": False, "message": "unknown_error", "raw": None}

    # ------------------------------
    # Bulk SMS
    # ------------------------------
    def send_bulk_sms(self, message, phone_numbers):
        """
        Send bulk SMS in chunks. Returns tuple: (success_count, errors)
        - success_count: int number of phones accepted/sent
        - errors: list of dicts { 'message': str, 'phones': [...], 'raw': ... }

        Important:
        - Do NOT call this from a web request synchronously if you may have many numbers.
        - Prefer scheduling this in a background worker (Celery/RQ).
        """
        chunk_size = 50
        success_count = 0
        errors = []

        # Clean and deduplicate/sanitize phone numbers
        cleaned = [self.clean_phone_number(p) for p in phone_numbers if p]
        cleaned = [p for p in cleaned if p]  # drop None
        # Optionally dedupe:
        # cleaned = list(dict.fromkeys(cleaned))

        for i in range(0, len(cleaned), chunk_size):
            chunk = cleaned[i : i + chunk_size]
            payload = {"senderID": self.sender_id, "message": message, "phones": ",".join(chunk)}
            try:
                r = self.session.post(self.BASE_URL_BULK, headers=self.headers, json=payload, timeout=self.timeout)
                r.raise_for_status()
                data = r.json() if r.text else {}
                if data.get("status"):
                    # Log each message. Be defensive: DB operations might fail, so log error but continue.
                    for p in chunk:
                        try:
                            client = Client.objects.filter(phone=p).first() if "Client" in globals() else None
                            MessageLog.objects.create(
                                client=client,
                                phone=p,
                                message=message,
                                reason="Bulk SMS",
                                send_status="sent",
                                delivery_status="pending",
                            )
                        except Exception as e:
                            # If DB write fails, record to errors but continue with other numbers
                            self.logger.exception(f"Failed to create MessageLog for {p}: {e}")
                            errors.append({"message": f"db_log_failed: {e}", "phones": [p], "raw": None})
                    success_count += len(chunk)
                else:
                    errors.append({"message": data.get("message"), "phones": chunk, "raw": data})
            except requests.Timeout as e:
                self.logger.error(f"Bulk chunk timeout for phones {chunk}: {e}")
                errors.append({"message": "timeout", "phones": chunk, "raw": None})
            except requests.ConnectionError as e:
                self.logger.error(f"Bulk chunk connection error for phones {chunk}: {e}")
                errors.append({"message": "connection_error", "phones": chunk, "raw": None})
            except requests.HTTPError as e:
                self.logger.error(f"Bulk chunk http error for phones {chunk}: {e}")
                try:
                    raw = r.json()
                except Exception:
                    raw = r.text if hasattr(r, "text") else None
                errors.append({"message": "http_error", "phones": chunk, "raw": raw})
            except Exception as e:
                self.logger.exception(f"Unexpected bulk send error for phones {chunk}: {e}")
                errors.append({"message": str(e), "phones": chunk, "raw": None})

        return success_count, errors

    # ------------------------------
    # Personalized (per-phone) SMS
    # ------------------------------
    def send_personalized_sms(self, message_pairs):
        """
        message_pairs: list of {'phone': '07..', 'message': 'text'} dicts.
        Returns: {'sent': [...phones...], 'failed': [...phones...], 'errors': [...]}
        - Like bulk, this should run in a background worker for large batches.
        """
        results = {"sent": [], "failed": [], "errors": []}
        for i in range(0, len(message_pairs), 50):
            chunk = message_pairs[i : i + 50]
            body = []
            for item in chunk:
                phone = self.clean_phone_number(item.get("phone"))
                msg_text = item.get("message")
                if phone and msg_text:
                    body.append({"phone": phone, "message": msg_text})
                else:
                    # Immediately mark invalid inputs as failed
                    if phone:
                        results["failed"].append(phone)
            if not body:
                continue
            payload = {"senderID": self.sender_id, "messageBody": body}
            try:
                resp = self.session.post(
                    self.BASE_URL_PERSONALIZED, headers=self.headers, json=payload, timeout=self.timeout
                )
                resp.raise_for_status()
                data = resp.json() if resp.text else {}
                if data.get("status"):
                    results["sent"].extend([entry["phone"] for entry in body])
                else:
                    results["failed"].extend([entry["phone"] for entry in body])
                    results["errors"].append({"message": data.get("message"), "raw": data})
            except requests.Timeout as e:
                self.logger.error(f"Personalized chunk timeout: {e}")
                results["failed"].extend([entry["phone"] for entry in body])
                results["errors"].append({"message": "timeout", "phones": [entry["phone"] for entry in body]})
            except requests.ConnectionError as e:
                self.logger.error(f"Personalized connection error: {e}")
                results["failed"].extend([entry["phone"] for entry in body])
                results["errors"].append({"message": "connection_error", "phones": [entry["phone"] for entry in body]})
            except requests.HTTPError as e:
                self.logger.error(f"Personalized http error: {e}")
                try:
                    raw = resp.json()
                except Exception:
                    raw = resp.text if hasattr(resp, "text") else None
                results["failed"].extend([entry["phone"] for entry in body])
                results["errors"].append({"message": "http_error", "raw": raw})
            except Exception as e:
                self.logger.exception(f"Unexpected error in send_personalized_sms: {e}")
                results["failed"].extend([entry["phone"] for entry in body])
                results["errors"].append({"message": str(e), "phones": [entry["phone"] for entry in body]})
        return results

    # ------------------------------
    # Delivery / status checks
    # ------------------------------
    def check_delivery_status(self, message_id):
        """
        Check delivery status for a message. Returns provider response as dict on success,
        or {'status': False, 'message': '...'} on failure. Adds timeout and error handling.
        """

        # Attempt to call the status endpoint if formatted URL is present, otherwise fall back to DLR
        try:
            # prefer specific status endpoint if message_id is supported
            url = self.BASE_URL_STATUS.format(message_id=message_id) if "{message_id}" in self.BASE_URL_STATUS else None
            if url:
                r = self.session.get(url, headers=self.headers, timeout=self.timeout)
            else:
                payload = {"messageId": message_id}
                r = self.session.post(self.BASE_URL_DLR, headers=self.headers, json=payload, timeout=self.timeout)

            r.raise_for_status()
            return r.json() if r.text else {"status": False, "message": "empty_response"}
        except requests.Timeout as e:
            self.logger.error(f"check_delivery_status timeout for {message_id}: {e}")
            return {"status": False, "message": "timeout"}
        except requests.ConnectionError as e:
            self.logger.error(f"check_delivery_status connection error for {message_id}: {e}")
            return {"status": False, "message": "connection_error"}
        except requests.HTTPError as e:
            self.logger.error(f"check_delivery_status http error for {message_id}: {e}")
            try:
                raw = r.json()
            except Exception:
                raw = r.text if hasattr(r, "text") else None
            return {"status": False, "message": "http_error", "raw": raw}
        except Exception as e:
            self.logger.exception(f"Unexpected error in check_delivery_status for {message_id}: {e}")
            return {"status": False, "message": "unknown_error"}

    # ------------------------------
    # Balance
    # ------------------------------
    def get_balance(self, force_refresh: bool = False):
        """
        Fetch the SMS provider balance with caching to avoid repeating calls.

        Returns a dict:
          - on success: { 'status': True, 'balance': <int/float>, 'raw': <provider response> }
          - on failure: { 'status': False, 'balance': 0, 'error': '...', 'raw': ... }

        Use force_refresh=True to bypass the cache.
        """
        # Use in-memory cache to avoid hitting SMS provider multiple times inside same request
        fetched_at = self._balance_cache.get("fetched_at")
        if not force_refresh and fetched_at:
            if datetime.utcnow() - fetched_at < self._balance_cache_ttl:
                return {"status": True, "balance": self._balance_cache["value"], "raw": None, "cached": True}

        try:
            r = self.session.get(self.BASE_URL_BALANCE, headers=self.headers, timeout=self.timeout)
            r.raise_for_status()
            data = r.json() if r.text else {}
            if data.get("status"):
                # Provider returned status True and balance info
                balance_value = data.get("balance") if "balance" in data else data
                # Update cache
                self._balance_cache["value"] = balance_value
                self._balance_cache["fetched_at"] = datetime.utcnow()
                return {"status": True, "balance": balance_value, "raw": data}
            else:
                self.logger.error(f"Balance fetch failed: {data.get('message')}")
                return {"status": False, "balance": 0, "error": data.get("message"), "raw": data}
        except requests.Timeout as e:
            self.logger.error(f"get_balance timeout: {e}")
            return {"status": False, "balance": 0, "error": "timeout"}
        except requests.ConnectionError as e:
            self.logger.error(f"get_balance connection error: {e}")
            return {"status": False, "balance": 0, "error": "connection_error"}
        except requests.HTTPError as e:
            self.logger.error(f"get_balance http error: {e}")
            try:
                raw = r.json()
            except Exception:
                raw = r.text if hasattr(r, "text") else None
            return {"status": False, "balance": 0, "error": "http_error", "raw": raw}
        except Exception as e:
            self.logger.exception(f"Unexpected error fetching balance: {e}")
            return {"status": False, "balance": 0, "error": str(e)}


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












