# apps/EasyDocs/bot/views_result.py
import logging
from django.http import JsonResponse, HttpRequest
from django.views.decorators.csrf import csrf_exempt
from django.core.cache import cache
from django.conf import settings
import json

logger = logging.getLogger(__name__)


def _normalize_result(request_id: str, result: dict) -> dict:
    """
    Ensure the result always has { ok, result: { answer, raw } }.
    Backward compatible with old Celery task outputs.
    """
    if not isinstance(result, dict):
        return {"ok": False, "error": str(result), "request_id": request_id}

    if "result" in result and isinstance(result["result"], dict):
        return result

    if "error" in result and isinstance(result["error"], str):
        return {"ok": False, "error": result["error"], "request_id": request_id}

    answer_text = None
    raw = {}

    if isinstance(result, str):
        answer_text = result
    elif isinstance(result, dict):
        answer_text = (
            result.get("answer")
            or result.get("message")
            or result.get("reply")
            or result.get("text")
        )
        raw = result

    if answer_text:
        return {"ok": True, "result": {"answer": answer_text, "raw": raw}, "request_id": request_id}

    return {"ok": False, "error": "No valid answer in result", "request_id": request_id}


@csrf_exempt
def poll_result(request: HttpRequest, request_id: str) -> JsonResponse:
    """
    Polling endpoint for bot results.
    Clients should call /api/bot/result/<request_id>/ to check if
    the response from n8n (via Celery task) is ready.
    """
    if request.method != "GET":
        return JsonResponse(
            {"ok": False, "error": "Invalid method", "request_id": request_id},
            status=405,
        )

    cache_key = f"bot:result:{request_id}"
    result = cache.get(cache_key)

    if result is None:
        logger.info("[bot.result] request_id=%s status=pending", request_id)
        return JsonResponse(
            {"ok": False, "status": "pending", "request_id": request_id},
            status=202,
        )

    normalized = _normalize_result(request_id, result)

    if normalized.get("ok"):
        # Top-level answer for legacy frontends
        try:
            normalized["answer"] = normalized["result"]["answer"]
        except Exception:
            pass
        logger.info("[bot.result] request_id=%s status=ready", request_id)
        return JsonResponse(normalized, status=200)

    logger.info("[bot.result] request_id=%s status=error", request_id)
    return JsonResponse(normalized, status=200)


@csrf_exempt
def store_result(request: HttpRequest, request_id: str) -> JsonResponse:
    """
    n8n callback: POST /api/bot/result/<request_id>/complete/
    """
    if request.method != "POST":
        return JsonResponse(
            {"ok": False, "error": "Invalid method", "request_id": request_id},
            status=405,
        )

    try:
        data = json.loads(request.body)
        result = data.get("result")
        if not result:
            raise ValueError("No result provided")
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e), "request_id": request_id}, status=400)

    # Store in cache (or DB) keyed by request_id
    cache_key = f"bot:result:{request_id}"
    cache.set(cache_key, result, timeout=60*60)  # 1 hour TTL

    return JsonResponse({"ok": True, "request_id": request_id})
