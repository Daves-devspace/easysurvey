# views.py (final similarity approach)
import json, requests
from pathlib import Path
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.cache import cache

HF_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
HF_API_URL = f"https://api-inference.huggingface.co/models/{HF_MODEL}"
KB_FILE = Path(settings.BASE_DIR) / "static" / "assets" / "json" / "knowledgeBase.json"

CHUNK_SIZE = 40
CACHE_TTL = 60 * 60  # 1 hour

def load_kb():
    if not KB_FILE.exists():
        return []
    with KB_FILE.open(encoding="utf-8") as f:
        kb = json.load(f)
    return [(e["question"], e["answer"]) for e in kb if "question" in e]

def chunk(lst, n):
    for i in range(0, len(lst), n):
        yield i, lst[i:i+n]

@csrf_exempt
def get_similarity(request):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid method"}, status=405)

    try:
        data = json.loads(request.body)
        user_text = (data.get("source_sentence") or data.get("text") or "").strip()
    except Exception:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    if not user_text:
        return JsonResponse({"error": "Missing query text"}, status=400)

    cache_key = f"ques::{user_text.lower()}"
    cached = cache.get(cache_key)
    if cached:
        return JsonResponse(cached)

    kb = load_kb()
    if not kb:
        return JsonResponse({"error": "Missing knowledge base"}, status=500)

    headers = {"Authorization": f"Bearer {settings.HF_API_KEY}", "Content-Type": "application/json"}
    best_score, best_answer = -1, None
    questions = [q for q, a in kb]

    for offset, chunk_qs in chunk(questions, CHUNK_SIZE):
        try:
            resp = requests.post(HF_API_URL, headers=headers, json={"inputs": {"source_sentence": user_text, "sentences": chunk_qs}}, timeout=30)
            if resp.status_code != 200:
                return JsonResponse({"error": "HF error", "details": resp.text}, status=500)
            scores = resp.json()
        except Exception as e:
            return JsonResponse({"error": f"HF call failed: {e}"}, status=500)

        for idx, sc in enumerate(scores):
            if isinstance(sc, (float, int)) and sc > best_score:
                best_score = sc
                best_answer = kb[offset + idx][1]

    response = {
        "answer": best_answer,
        "score": best_score
    }
    cache.set(cache_key, response, CACHE_TTL)
    return JsonResponse(response)
