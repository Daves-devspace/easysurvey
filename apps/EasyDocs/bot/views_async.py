# apps/EasyDocs/bot/views_async.py
import json
import logging
import uuid
import asyncio
import httpx
from django.conf import settings
from django.http import JsonResponse, HttpRequest
from django.views.decorators.csrf import csrf_exempt
from asgiref.sync import sync_to_async
from . import circuit_breaker

logger = logging.getLogger(__name__)

N8N_WEBHOOK_URL = getattr(settings, "N8N_BOT_WEBHOOK_URL", "https://n8n.example.com/webhook/bot")
BOT_SECRET = getattr(settings, "BOT_SECRET", "replace-with-strong-secret")
CLIENT_SECRET = getattr(settings, "CLIENT_SECRET", None)

CONNECT_TIMEOUT = float(getattr(settings, "BOT_CONNECT_TIMEOUT", 3.0))
READ_TIMEOUT = float(getattr(settings, "BOT_READ_TIMEOUT", 8.0))
TOTAL_TIMEOUT = float(getattr(settings, "BOT_TOTAL_TIMEOUT", 10.0))
MAX_STATUS_RETRIES = int(getattr(settings, "BOT_STATUS_RETRIES", 1))
RETRY_BACKOFF = float(getattr(settings, "BOT_REQUEST_RETRY_BACKOFF", 0.2))
MAX_PAYLOAD_BYTES = int(getattr(settings, "BOT_MAX_PAYLOAD_BYTES", 32 * 1024))


def _mask_headers(hdrs):
    return {
        k: "[REDACTED]"
        if k.lower() in ("authorization", "x-bot-secret", "x-client-secret", "api-key")
        else v
        for k, v in (hdrs or {}).items()
    }


def _normalize_response(resp) -> dict:
    """
    Normalize n8n response to always have result.answer.
    Keeps raw data for debugging.
    """
    try:
        if resp.headers.get("Content-Type", "").startswith("application/json"):
            data = resp.json()
            answer_text = (
                data.get("answer")
                or data.get("message")
                or data.get("reply")
                or data.get("text")
                or None
            )
            return {"ok": True, "result": {"answer": answer_text, "raw": data}}
        else:
            return {"ok": True, "result": {"answer": resp.text, "raw": {}}}
    except Exception as exc:
        logger.exception("[bot.async] failed to normalize response: %s", exc)
        return {"ok": True, "result": {"answer": resp.text, "raw": {}}}


@csrf_exempt
async def forward_to_n8n_async(request: HttpRequest) -> JsonResponse:
    """
    Async Django view that forwards bot payload to n8n.
    - Circuit breaker, retries, structured logging
    """
    request_id = str(uuid.uuid4())
    logger.info("[bot.async] request_id=%s method=%s", request_id, request.method)

    if request.method != "POST":
        return JsonResponse({"error": "Invalid method", "request_id": request_id}, status=405)

    # --- auth ---
    incoming_client_secret = request.headers.get("X-Client-Secret")
    if CLIENT_SECRET and incoming_client_secret:
        if incoming_client_secret != CLIENT_SECRET:
            return JsonResponse({"error": "Unauthorized", "request_id": request_id}, status=401)
    else:
        user = await sync_to_async(lambda: getattr(request, "user", None))()
        if user is None or not getattr(user, "is_authenticated", False):
            return JsonResponse({"error": "Authentication required", "request_id": request_id}, status=401)

    # --- payload size / JSON check ---
    body = await request.body
    if len(body) > MAX_PAYLOAD_BYTES:
        return JsonResponse({"error": "Payload too large", "request_id": request_id}, status=413)

    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON", "request_id": request_id}, status=400)

    # redact sensitive keys
    for p in ("ssn", "password", "credit_card"):
        if p in payload:
            payload[p] = "[REDACTED]"

    if circuit_breaker.is_open():
        return JsonResponse({"error": "Assistant unavailable (circuit open)", "request_id": request_id}, status=503)

    headers = {
        "Content-Type": "application/json",
        "X-Bot-Secret": BOT_SECRET,
        "X-Request-ID": request_id,
    }
    logger.info(
        "[bot.async] request_id=%s forwarding headers=%s payload_keys=%s",
        request_id,
        _mask_headers(headers),
        list(payload.keys()),
    )

    timeout = httpx.Timeout(timeout=TOTAL_TIMEOUT, connect=CONNECT_TIMEOUT, read=READ_TIMEOUT)

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(MAX_STATUS_RETRIES + 1):
            try:
                resp = await client.post(N8N_WEBHOOK_URL, json=payload, headers=headers)
                if resp.status_code >= 400:
                    await asyncio.sleep(RETRY_BACKOFF * (attempt + 1))
                    continue
                circuit_breaker.record_success()
                return JsonResponse(_normalize_response(resp))
            except Exception as exc:
                circuit_breaker.record_failure()
                logger.exception("[bot.async] request_id=%s exception: %s", request_id, exc)

    return JsonResponse({"error": "Failed to reach n8n", "request_id": request_id}, status=502)
