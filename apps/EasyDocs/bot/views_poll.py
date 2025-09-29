# apps/EasyDocs/views_poll.py
from django.http import JsonResponse
from django.core.cache import cache

def poll_result(request, request_id):
    key = f"bot:result:{request_id}"
    data = cache.get(key)
    if not data:
        return JsonResponse({"status": "pending", "request_id": request_id}, status=202)
    return JsonResponse(data)
