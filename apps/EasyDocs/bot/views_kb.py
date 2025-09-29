import json
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.conf import settings
import os

@require_GET
def knowledge_base(request):
    """
    Serves the enriched KB JSON with embeddings.
    """
    kb_path = os.path.join(
        settings.BASE_DIR,
        "static/assets/json/knowledgeBase_with_embeddings.json"
    )

    try:
        with open(kb_path, "r", encoding="utf-8") as f:
            kb = json.load(f)
    except FileNotFoundError:
        return JsonResponse({"error": "KB not found"}, status=404)

    return JsonResponse(kb, safe=False)  # safe=False since kb is a list
