# # apps/EasyDocs/bot.py  (replace forward_to_n8n with this)
# import json
# import logging
# import uuid
# from typing import Dict

# import requests
# from requests.adapters import HTTPAdapter
# from requests.exceptions import (
#     SSLError,
#     ConnectTimeout,
#     ReadTimeout,
#     ConnectionError as RequestsConnectionError,
#     HTTPError,
#     RequestException,
# )
# from urllib3.util.retry import Retry

# from django.conf import settings
# from django.http import JsonResponse, HttpRequest
# from django.views.decorators.csrf import csrf_exempt

# logger = logging.getLogger(__name__)

# N8N_WEBHOOK_URL = getattr(settings, "N8N_BOT_WEBHOOK_URL", "https://n8n.liorixdigital.com/webhook/bot")
# BOT_SECRET = getattr(settings, "BOT_SECRET", "replace-with-strong-secret")
# CLIENT_SECRET = getattr(settings, "CLIENT_SECRET", None)

# # Timeouts and retries (tunable)
# CONNECT_TIMEOUT = float(getattr(settings, "BOT_CONNECT_TIMEOUT", 3.0))   # seconds to establish TCP
# READ_TIMEOUT = float(getattr(settings, "BOT_READ_TIMEOUT", 8.0))       # seconds to wait for response body
# REQUEST_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)                     # tuple for requests timeout
# REQUEST_RETRIES = int(getattr(settings, "BOT_REQUEST_RETRIES", 1))     # only retry on idempotent status codes, not connect
# RETRY_BACKOFF = float(getattr(settings, "BOT_REQUEST_RETRY_BACKOFF", 0.2))
# MAX_PAYLOAD_BYTES = int(getattr(settings, "BOT_MAX_PAYLOAD_BYTES", 32 * 1024))


# def _create_requests_session() -> requests.Session:
#     """
#     Create a requests session with a Retry policy:
#     - Do NOT retry connect attempts (connect=0) — prevents long blocking on dead hosts.
#     - Allow a small number of retries for 429/502/503/504 status codes.
#     """
#     session = requests.Session()
#     retry = Retry(
#         total=REQUEST_RETRIES,
#         connect=0,         # important: do not retry connect -> avoids lengthy blocking
#         read=REQUEST_RETRIES,
#         status=REQUEST_RETRIES,
#         backoff_factor=RETRY_BACKOFF,
#         status_forcelist=[429, 502, 503, 504],
#         allowed_methods=["HEAD", "GET", "POST", "PUT", "OPTIONS"],
#         raise_on_status=False,
#     )
#     adapter = HTTPAdapter(max_retries=retry)
#     session.mount("https://", adapter)
#     session.mount("http://", adapter)
#     return session


# def _mask_headers(hdrs: Dict[str, str]) -> Dict[str, str]:
#     masked = {}
#     for k, v in (hdrs or {}).items():
#         if k.lower() in ("authorization", "x-bot-secret", "x-client-secret", "api-key", "apikey"):
#             masked[k] = "[REDACTED]"
#         else:
#             masked[k] = v
#     return masked


# @csrf_exempt
# def forward_to_n8n(request: HttpRequest) -> JsonResponse:
#     request_id = str(uuid.uuid4())
#     logger.info("[bot.forward] request_id=%s method=%s path=%s", request_id, request.method, request.path)

#     if request.method != "POST":
#         logger.warning("[bot.forward] request_id=%s invalid method", request_id)
#         return JsonResponse({"error": "Invalid method", "request_id": request_id}, status=405)

#     # Auth: allow service secret or authenticated user
#     incoming_client_secret = request.headers.get("X-Client-Secret")
#     if CLIENT_SECRET and incoming_client_secret:
#         if incoming_client_secret != CLIENT_SECRET:
#             logger.warning("[bot.forward] request_id=%s invalid X-Client-Secret", request_id)
#             return JsonResponse({"error": "Unauthorized", "request_id": request_id}, status=401)
#     else:
#         user = getattr(request, "user", None)
#         if user is None or not getattr(user, "is_authenticated", False):
#             logger.warning("[bot.forward] request_id=%s unauthenticated", request_id)
#             return JsonResponse({"error": "Authentication required", "request_id": request_id}, status=401)

#     body_bytes = request.body or b""
#     if len(body_bytes) > MAX_PAYLOAD_BYTES:
#         logger.warning("[bot.forward] request_id=%s payload too large (%d bytes)", request_id, len(body_bytes))
#         return JsonResponse({"error": "Payload too large", "request_id": request_id}, status=413)

#     try:
#         payload = json.loads(body_bytes.decode("utf-8"))
#     except Exception as exc:
#         logger.warning("[bot.forward] request_id=%s invalid JSON: %s", request_id, exc)
#         return JsonResponse({"error": "Invalid JSON", "request_id": request_id}, status=400)

#     # sanitize keys
#     for p in ("ssn", "password", "credit_card", "card_number"):
#         if p in payload:
#             payload[p] = "[REDACTED]"

#     forward_headers = {
#         "Content-Type": "application/json",
#         "X-Bot-Secret": BOT_SECRET,
#         "X-Request-ID": request_id,
#         "X-Forwarded-User": str(getattr(request, "user", None))[:128] if getattr(request, "user", None) else "anonymous",
#     }

#     session = _create_requests_session()

#     try:
#         logger.info(
#             "[bot.forward] request_id=%s forwarding to n8n url=%s headers=%s payload_keys=%s",
#             request_id,
#             N8N_WEBHOOK_URL,
#             _mask_headers(forward_headers),
#             list(payload.keys()),
#         )

#         # Use explicit (connect, read) timeouts to avoid long blocking
#         resp = session.post(N8N_WEBHOOK_URL, json=payload, headers=forward_headers, timeout=REQUEST_TIMEOUT)

#         # If status indicates error, log body preview
#         if resp.status_code >= 400:
#             body_preview = (resp.text[:2000] if resp.text else "")
#             logger.error(
#                 "[bot.forward] request_id=%s n8n returned HTTP status=%s body_preview=%s",
#                 request_id,
#                 resp.status_code,
#                 body_preview,
#             )
#             # Helpful hint for 404 specifically
#             if resp.status_code == 404:
#                 return JsonResponse({
#                     "error": "Upstream webhook not found (n8n returned 404).",
#                     "hint": "Ensure the n8n workflow is active and N8N_BOT_WEBHOOK_URL matches the production webhook URL.",
#                     "request_id": request_id,
#                     "n8n_body": body_preview[:1000],
#                 }, status=502)
#             return JsonResponse({"error": "Upstream HTTP error from n8n", "status": resp.status_code, "request_id": request_id}, status=502)

#         # Try parse JSON
#         content_type = resp.headers.get("Content-Type", "")
#         if "application/json" in content_type:
#             data = resp.json()
#             if isinstance(data, dict):
#                 data.setdefault("request_id", request_id)
#             return JsonResponse(data)
#         else:
#             return JsonResponse({"message": resp.text, "request_id": request_id})

#     except ConnectTimeout as exc:
#         logger.exception("[bot.forward] request_id=%s connect timeout contacting n8n: %s", request_id, exc)
#         return JsonResponse({"error": "Timeout connecting to n8n", "request_id": request_id}, status=504)

#     except ReadTimeout as exc:
#         logger.exception("[bot.forward] request_id=%s read timeout contacting n8n: %s", request_id, exc)
#         return JsonResponse({"error": "Timeout waiting for n8n response", "request_id": request_id}, status=504)

#     except SSLError as exc:
#         logger.exception("[bot.forward] request_id=%s TLS/SSL error contacting n8n: %s", request_id, exc)
#         return JsonResponse({"error": "TLS/SSL error contacting n8n (check certs)", "request_id": request_id}, status=502)

#     except RequestsConnectionError as exc:
#         logger.exception("[bot.forward] request_id=%s connection error contacting n8n: %s", request_id, exc)
#         return JsonResponse({"error": "Connection error contacting n8n", "request_id": request_id}, status=502)

#     except RequestException as exc:
#         logger.exception("[bot.forward] request_id=%s requests exception contacting n8n: %s", request_id, exc)
#         return JsonResponse({"error": "Failed to reach n8n webhook", "request_id": request_id}, status=502)

#     except Exception as exc:
#         logger.exception("[bot.forward] request_id=%s unexpected error: %s", request_id, exc)
#         return JsonResponse({"error": "Internal server error", "request_id": request_id}, status=500)
