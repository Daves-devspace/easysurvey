# apps/EasyDocs/bot/views_enqueue.py
import json, uuid, logging, requests
from django.http import JsonResponse, HttpRequest
from django.views.decorators.csrf import csrf_exempt
from django.core.cache import cache
from django.conf import settings
from .tasks import forward_to_n8n_task

logger = logging.getLogger(__name__)

MAX_PAYLOAD_BYTES = int(getattr(settings, "BOT_MAX_PAYLOAD_BYTES", 32 * 1024))
N8N_WEBHOOK_URL = getattr(settings, "N8N_WEBHOOK_URL", "https://n8n.liorixdigital.com/webhook/bot")
N8N_BOT_SECRET = getattr(settings, "N8N_BOT_SECRET", "wrQhkCSfZA0358br6fCovolp3kPhcZ2kpFUtxjIieBk")

@csrf_exempt
def enqueue_forward(request: HttpRequest) -> JsonResponse:
    """
    Try direct n8n call first, fallback to Celery queue if it fails.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Invalid method"}, status=405)

    try:
        body = request.body
        if len(body) > MAX_PAYLOAD_BYTES:
            return JsonResponse({"error": "Payload too large"}, status=413)
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    request_id = str(uuid.uuid4())
    user = getattr(request, "user", None)
    user_str = str(user) if user and getattr(user, "is_authenticated", False) else "anonymous"

    logger.info("[bot.enqueue] request_id=%s user=%s payload_keys=%s", request_id, user_str, list(payload.keys()))

    # Add request_id to payload
    payload['request_id'] = request_id
    if not payload.get('username'):
        payload['username'] = user_str

    # Try direct n8n call first (fast path)
    try:
        headers = {
            'Content-Type': 'application/json',
            'X-Bot-Secret': N8N_BOT_SECRET
        }
        
        response = requests.post(
            N8N_WEBHOOK_URL,
            json=payload,
            headers=headers,
            timeout=10  # 10 second timeout for immediate response
        )
        
        if response.status_code == 200:
            try:
                result = response.json()
                logger.info("[bot.enqueue] Direct n8n success: request_id=%s", request_id)
                
                # If we got a complete result, return it immediately
                if result.get("ok") and result.get("result") and result["result"].get("answer"):
                    return JsonResponse(result, status=200)
                    
            except json.JSONDecodeError:
                logger.warning("[bot.enqueue] Invalid JSON from n8n: request_id=%s", request_id)
                
    except requests.RequestException as e:
        logger.warning("[bot.enqueue] Direct n8n failed: request_id=%s error=%s", request_id, str(e))

    # Fallback: enqueue Celery task for deferred processing
    logger.info("[bot.enqueue] Falling back to Celery: request_id=%s", request_id)
    forward_to_n8n_task.delay(request_id, payload, user_str)

    # return poll URL
    poll_url = f"/api/bot/result/{request_id}/"
    return JsonResponse({"request_id": request_id, "poll_url": poll_url}, status=202)