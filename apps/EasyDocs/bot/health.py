# apps/EasyDocs/health.py
import logging
import requests
from django.http import JsonResponse
from django.conf import settings
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)

N8N_WEBHOOK_URL = getattr(settings, "N8N_BOT_WEBHOOK_URL", None)

@require_GET
def bot_probe(request):
    """Quick probe to see if n8n webhook is reachable (non-blocking short timeout)."""
    if not N8N_WEBHOOK_URL:
        return JsonResponse({"ok": False, "reason": "N8N_BOT_WEBHOOK_URL not configured"}, status=500)
    try:
        resp = requests.head(N8N_WEBHOOK_URL, timeout=(2, 4), allow_redirects=True, verify=getattr(settings, "VERIFY_N8N_CERT", True))
        return JsonResponse({"ok": resp.status_code < 400, "status_code": resp.status_code})
    except Exception as exc:
        logger.exception("bot probe failure: %s", exc)
        return JsonResponse({"ok": False, "error": str(exc)}, status=503)
