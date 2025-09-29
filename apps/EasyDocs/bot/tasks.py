# apps/EasyDocs/bot/tasks.py
import json
import logging
import httpx
from celery import shared_task
from django.core.cache import cache
from django.conf import settings

logger = logging.getLogger(__name__)

N8N_WEBHOOK_URL = getattr(settings, "N8N_BOT_WEBHOOK_URL", "https://n8n.example.com/webhook/bot")
BOT_SECRET = getattr(settings, "BOT_SECRET", "replace-with-strong-secret")

CACHE_TTL = int(getattr(settings, "BOT_RESULT_TTL", 300))  # seconds

def _normalize_result(request_id: str, ok: bool = True, payload=None, error=None):
    """
    Normalize any n8n result into { ok, result: { answer, raw } }.
    """
    if not ok:
        return {"ok": False, "error": str(error), "request_id": request_id}

    answer_text = None
    raw = {}

    if isinstance(payload, str):
        answer_text = payload.strip() or "No answer available"
        raw = {"text": payload}
    elif isinstance(payload, dict):
        answer_text = (
            payload.get("answer")
            or payload.get("message")
            or payload.get("reply")
            or payload.get("text")
        )
        if not answer_text:
            answer_text = "No answer available"
        raw = payload
    else:
        answer_text = str(payload)

    return {"ok": True, "result": {"answer": answer_text, "raw": raw}, "request_id": request_id}


@shared_task
def forward_to_n8n_task(request_id: str, payload: dict, user_str: str = "anonymous"):
    """
    Celery task: forward payload to n8n webhook and cache normalized result.
    """
    logger.info("[bot.task] start request_id=%s", request_id)

    headers = {
        "Content-Type": "application/json",
        "X-Bot-Secret": BOT_SECRET,
        "X-Request-ID": request_id,
        "X-User": user_str,
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(N8N_WEBHOOK_URL, json=payload, headers=headers)

        logger.info("[bot.task] request_id=%s upstream_status=%s", request_id, resp.status_code)

        if resp.headers.get("content-type", "").startswith("application/json"):
            try:
                data = resp.json()
            except Exception as e:
                logger.warning("[bot.task] request_id=%s invalid JSON: %s", request_id, e)
                data = {"error": f"Invalid JSON: {e}"}
            cache.set(
                f"bot:result:{request_id}",
                _normalize_result(request_id, ok=True, payload=data),
                CACHE_TTL,
            )
        else:
            raw_text = resp.text if resp is not None else ""
            snippet = (raw_text[:500] + "...") if len(raw_text) > 500 else raw_text
            logger.info(
                "[bot.task] request_id=%s cached raw text result len=%d snippet=%r",
                request_id, len(raw_text), snippet
            )
            cache.set(
                f"bot:result:{request_id}",
                _normalize_result(request_id, ok=True, payload=raw_text),
                CACHE_TTL,
            )

    except Exception as exc:
        logger.exception("[bot.task] request_id=%s failed: %s", request_id, exc)
        cache.set(
            f"bot:result:{request_id}",
            _normalize_result(request_id, ok=False, error=str(exc)),
            CACHE_TTL,
        )
