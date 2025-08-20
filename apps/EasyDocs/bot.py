# apps/EasyDocs/views_bot.py
import json
import hashlib
import time
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor

import requests
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.cache import cache

import logging
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# Config
HF_API_KEY = getattr(settings, "HF_API_KEY", "")
HF_SIMILARITY_MODEL = getattr(settings, "HF_SIMILARITY_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
HF_ZERO_SHOT_MODEL = getattr(settings, "HF_ZERO_SHOT_MODEL", "facebook/bart-large-mnli")

BASE_DIR = getattr(settings, "BASE_DIR", Path("."))
KB_FILE = Path(BASE_DIR) / "static" / "assets" / "json" / "knowledgeBase.json"

CHUNK_SIZE = getattr(settings, "HF_CHUNK_SIZE", 40)
SIMILARITY_MIN_SCORE = getattr(settings, "SIMILARITY_MIN_SCORE", 0.28)
INTENT_CONF_THRESH = getattr(settings, "INTENT_CONF_THRESH", 0.60)
CACHE_TTL = getattr(settings, "CACHE_TTL", 60 * 60)
REQUEST_TIMEOUT = getattr(settings, "HF_TIMEOUT", 15)
MAX_WORKERS = getattr(settings, "MAX_WORKERS", 3)

SIMILARITY_URL = f"https://api-inference.huggingface.co/models/{HF_SIMILARITY_MODEL}"
ZERO_SHOT_URL = f"https://api-inference.huggingface.co/models/{HF_ZERO_SHOT_MODEL}"
HEADERS = {"Authorization": f"Bearer {HF_API_KEY}", "Content-Type": "application/json"}

executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

# ---------------------------------------------------------------------
# Utilities
def hf_post(url: str, payload: dict, timeout: int = REQUEST_TIMEOUT) -> Optional[dict]:
    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            r = requests.post(url, headers=HEADERS, json=payload, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 503 and attempt < max_retries:
                time.sleep(2 ** attempt)
                continue
            logger.warning(f"HF POST failed [{r.status_code}] attempt {attempt+1} → {r.text[:200]}")
        except requests.exceptions.Timeout:
            logger.error(f"HF POST timeout attempt {attempt+1} → {url}")
            if attempt < max_retries:
                time.sleep(1)
        except Exception as e:
            logger.exception(f"HF POST exception attempt {attempt+1}: {e}")
            break
    return None

def hash_text(t: str) -> str:
    return hashlib.md5(t.encode("utf-8")).hexdigest()[:16]

def preprocess_text(text: str) -> str:
    return ' '.join(text.lower().split())

# ---------------------------------------------------------------------
# Load KB
def _load_kb() -> Tuple[List[Tuple[str, str]], Dict[str, str]]:
    if not KB_FILE.exists():
        return [], {}
    with KB_FILE.open(encoding="utf-8") as fh:
        data = json.load(fh)
    kb_list, kb_dict = [], {}
    for e in data:
        if not e.get("question"):
            continue
        q = e["question"].strip()
        a = e.get("answer", "").strip()
        kb_list.append((q, a))
        kb_dict[preprocess_text(q)] = a
    return kb_list, kb_dict

KB, KB_DICT = _load_kb()

GREETING_PATTERNS = {"hi", "hello", "hey", "morning", "evening", "good morning", "good afternoon", "good evening"}
THANKS_PATTERNS = {"thanks", "thank you", "cheers", "ty", "thx", "appreciated"}
CORRECTION_PATTERNS = {"i meant", "sorry", "correction", "actually", "no i meant"}
EXIT_PATTERNS = {"no", "nothing", "nope", "nah"}

# Multi-turn session memory
USER_SESSION = {}

def set_awaiting_confirmation(username: str, value: bool):
    if username:
        USER_SESSION.setdefault(username, {})
        USER_SESSION[username]["awaiting_confirmation"] = value

def get_awaiting_confirmation(username: str) -> bool:
    return USER_SESSION.get(username, {}).get("awaiting_confirmation", False)

def chunk_list(lst: List[str], n: int):
    for i in range(0, len(lst), n):
        yield i, lst[i:i+n]

# ---------------------------------------------------------------------
# Intent Detection
INTENT_LABELS = ["greeting", "correction", "selection", "thanks", "question", "complaint", "urgent", "unknown"]

def cheap_intent(text: str) -> Optional[Dict[str, Any]]:
    normalized = preprocess_text(text)
    if normalized in KB_DICT:
        return {"label": "exact_match", "score": 1.0}
    low = text.lower().strip()
    text_len = len(text)
    if (text_len == 1 and text.isalpha()) or (text.isdigit() and text_len <= 2):
        return {"label": "selection", "score": 0.95}
    if text_len <= 50 and any(p in low for p in CORRECTION_PATTERNS):
        return {"label": "correction", "score": 0.7}
    if text_len <= 25:
        if any(p in low for p in GREETING_PATTERNS):
            return {"label": "greeting", "score": 0.6}
        if any(p in low for p in THANKS_PATTERNS):
            return {"label": "thanks", "score": 0.6}
    return None

def detect_intent(text: str) -> Dict[str, Any]:
    local_result = cheap_intent(text)
    if local_result:
        return local_result
    if not HF_API_KEY or not HF_ZERO_SHOT_MODEL:
        return {"label": "question", "score": 0.3}
    key = f"int::{hash_text(text)}"
    if cached := cache.get(key):
        return cached
    resp = hf_post(ZERO_SHOT_URL, {"inputs": text, "parameters": {"candidate_labels": INTENT_LABELS}})
    if resp and "labels" in resp and resp["labels"]:
        result = {"label": resp["labels"][0], "score": float(resp["scores"][0])}
        cache.set(key, result, CACHE_TTL)
        return result
    return {"label": "question", "score": 0.3}

# ---------------------------------------------------------------------
# Similarity Search
def similarity_search(user_text: str) -> Dict[str, Any]:
    if not KB:
        return {"answer": None, "score": 0.0, "method": "no_kb"}
    normalized = preprocess_text(user_text)
    if normalized in KB_DICT:
        return {"answer": KB_DICT[normalized], "score": 1.0, "method": "exact"}
    key = f"sim::{hash_text(user_text)}"
    if cached := cache.get(key):
        cached["method"] = "cached"
        return cached
    if not HF_API_KEY:
        return {"answer": None, "score": 0.0, "method": "no_api"}
    questions = [q for q, _ in KB]
    best = {"answer": None, "score": -1.0}
    successful_chunks = 0
    for offset, chunk in chunk_list(questions, CHUNK_SIZE):
        resp = hf_post(SIMILARITY_URL, {"inputs": {"source_sentence": user_text, "sentences": chunk}})
        if not resp or not isinstance(resp, list):
            continue
        successful_chunks += 1
        for i, score in enumerate(resp):
            try:
                score = float(score)
                if score > best["score"]:
                    best = {"answer": KB[offset+i][1], "score": score}
            except (ValueError, TypeError, IndexError):
                continue
    best["method"] = f"similarity_{successful_chunks}_chunks"
    if best["score"] >= SIMILARITY_MIN_SCORE:
        cache.set(key, best, CACHE_TTL)
    return best

# ---------------------------------------------------------------------
# Fallbacks
FALLBACK_RESPONSES = {
    "default": "I couldn't find a strong match. Try asking about Clients, Services, Bookings, Documents, Accounts, or Employees.",
    "with_username": "Hi {username}, I couldn't find a strong match. Try asking about Clients, Services, Bookings, Documents, Accounts, or Employees.",
    "api_unavailable": "The system is temporarily busy. Please try again in a moment, or ask about Clients, Services, Bookings, Documents, Accounts, or Employees.",
}

def get_fallback_response(username: str = "", method: str = "default") -> str:
    if method == "no_api":
        return FALLBACK_RESPONSES["api_unavailable"]
    if username:
        return FALLBACK_RESPONSES["with_username"].format(username=username)
    return FALLBACK_RESPONSES["default"]

def is_exit_intent(text: str) -> bool:
    return any(word in text.lower() for word in EXIT_PATTERNS)

# ---------------------------------------------------------------------
# Main Endpoint
@csrf_exempt
def get_similarity(request):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid method"}, status=405)
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    raw_text = (payload.get("source_sentence") or payload.get("text") or "").strip()
    if not raw_text:
        return JsonResponse({"error": "Missing query text"}, status=400)
    if len(raw_text) > 1000:
        return JsonResponse({"error": "Query too long"}, status=400)
    username = payload.get("username") or ""
    if hasattr(request, "user") and getattr(request.user, "is_authenticated", False):
        username = getattr(request.user, "username", username)

    # -----------------------
    # Multi-turn "Anything else?" handling
    if username and get_awaiting_confirmation(username):
        if is_exit_intent(raw_text):
            set_awaiting_confirmation(username, False)
            return JsonResponse({
                "answer": f"Alright {username}, glad I could help! Have a great day 🚀",
                "score": 1.0,
                "intent": {"label": "exit", "score": 1.0},
                "method": "exit_intent"
            })
        # Otherwise, continue processing and reset flag
        set_awaiting_confirmation(username, False)

    # -----------------------
    intent = detect_intent(raw_text)

    if intent["label"] == "greeting":
        response = f"Hi {username or ''}! How can I help you today?".strip()
        return JsonResponse({"answer": response, "score": 1.0, "intent": intent, "method": "greeting"})

    if intent["label"] == "thanks":
        response = f"You're welcome {username or ''}! Anything else I can help with?".strip()
        set_awaiting_confirmation(username, True)
        return JsonResponse({"answer": response, "score": 1.0, "intent": intent, "method": "thanks"})

    if intent["label"] == "exact_match":
        normalized = preprocess_text(raw_text)
        answer = KB_DICT.get(normalized)
        if answer:
            return JsonResponse({"answer": answer, "score": 1.0, "intent": intent, "method": "exact_match"})

    sim = similarity_search(raw_text)
    answer, score = sim["answer"], sim["score"]

    if not answer or score < SIMILARITY_MIN_SCORE:
        fallback = get_fallback_response(username, sim.get("method", "default"))
        return JsonResponse({"answer": fallback, "score": score, "intent": intent, "method": sim.get("method", "fallback")})

    return JsonResponse({"answer": answer, "score": score, "intent": intent, "method": sim.get("method", "similarity")})

# ---------------------------------------------------------------------
# Health Check
@csrf_exempt  
def bot_health(request):
    return JsonResponse({
        "status": "healthy",
        "kb_size": len(KB),
        "cache_backend": str(cache.__class__.__name__),
        "hf_api_configured": bool(HF_API_KEY)
    })
