# apps/EasyDocs/views_bot.py
"""
Production Hybrid Chatbot - Best Performance
- HuggingFace: Fast semantic search (sentence-transformers)
- OpenRouter: Natural answer refinement (optional)
- Redis: Aggressive caching for speed
"""

import json
import hashlib
import time
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, asdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.cache import cache

import logging
logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

class Config:
    # HuggingFace (for semantic search)
    HF_API_KEY = getattr(settings, "HF_API_KEY", "")
    HF_SIMILARITY_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
    HF_ZERO_SHOT_MODEL = "facebook/bart-large-mnli"
    HF_SIMILARITY_URL = f"https://api-inference.huggingface.co/models/{HF_SIMILARITY_MODEL}"
    HF_ZERO_SHOT_URL = f"https://api-inference.huggingface.co/models/{HF_ZERO_SHOT_MODEL}"
    
    # OpenRouter (for refinement)
    OPENROUTER_KEY = getattr(settings, "OPENROUTER_KEY", "")
    OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
    OPENROUTER_MODEL = "mistralai/mistral-7b-instruct:free"
    
    # Files
    BASE_DIR = getattr(settings, "BASE_DIR", Path("."))
    KB_FILE = Path(BASE_DIR) / "static" / "assets" / "json" / "knowledgeBase.json"
    
    # Performance
    CHUNK_SIZE = 40  # Questions per HF API call
    MAX_WORKERS = 4  # Parallel chunk processing
    REQUEST_TIMEOUT = 12
    CACHE_TTL = 3600  # 1 hour
    
    # Thresholds
    SIMILARITY_MIN_SCORE = 0.30  # Minimum match confidence
    HIGH_CONFIDENCE_SCORE = 0.65  # When to refine with LLM
    
    # Features
    ENABLE_REFINEMENT = True  # Set False for max speed (no OpenRouter)
    REFINE_HIGH_CONFIDENCE_ONLY = True  # Only refine good matches

config = Config()

# ============================================================================
# Data Models
# ============================================================================

@dataclass
class Intent:
    label: str
    confidence: float
    method: str

@dataclass
class SearchResult:
    answer: str
    score: float
    question: str
    method: str
    chunks_searched: int = 0

@dataclass
class BotResponse:
    answer: str
    confidence: float
    intent: Intent
    method: str
    metadata: Dict[str, Any]
    cached: bool = False

# ============================================================================
# Knowledge Base
# ============================================================================

class KnowledgeBase:
    """Load and manage KB with exact matching"""
    
    def __init__(self):
        self.qa_pairs: List[Tuple[str, str]] = []
        self.exact_match_dict: Dict[str, str] = {}
        self._load()
    
    def _load(self):
        if not config.KB_FILE.exists():
            logger.warning(f"KB file not found: {config.KB_FILE}")
            return
        
        try:
            with config.KB_FILE.open(encoding="utf-8") as f:
                data = json.load(f)
            
            for entry in data:
                q = entry.get("question", "").strip()
                a = entry.get("answer", "").strip()
                
                if q and a:
                    self.qa_pairs.append((q, a))
                    # Store normalized version for exact match
                    normalized = ' '.join(q.lower().split())
                    self.exact_match_dict[normalized] = a
            
            logger.info(f"✓ Loaded {len(self.qa_pairs)} KB entries")
        
        except Exception as e:
            logger.exception(f"Failed to load KB: {e}")
    
    def exact_match(self, query: str) -> Optional[str]:
        """Check for exact question match"""
        normalized = ' '.join(query.lower().split())
        return self.exact_match_dict.get(normalized)
    
    def get_questions(self) -> List[str]:
        return [q for q, _ in self.qa_pairs]
    
    def get_answer(self, index: int) -> str:
        return self.qa_pairs[index][1] if 0 <= index < len(self.qa_pairs) else ""

kb = KnowledgeBase()

# ============================================================================
# Cache Manager
# ============================================================================

class CacheManager:
    """Smart caching with versioned keys"""
    
    @staticmethod
    def hash_key(text: str) -> str:
        return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]
    
    @staticmethod
    def get_intent(text: str) -> Optional[Intent]:
        key = f"int:v2:{CacheManager.hash_key(text)}"
        data = cache.get(key)
        return Intent(**data) if data else None
    
    @staticmethod
    def set_intent(text: str, intent: Intent):
        key = f"int:v2:{CacheManager.hash_key(text)}"
        cache.set(key, asdict(intent), config.CACHE_TTL)
    
    @staticmethod
    def get_search(text: str) -> Optional[SearchResult]:
        key = f"search:v2:{CacheManager.hash_key(text)}"
        data = cache.get(key)
        return SearchResult(**data) if data else None
    
    @staticmethod
    def set_search(text: str, result: SearchResult):
        key = f"search:v2:{CacheManager.hash_key(text)}"
        cache.set(key, asdict(result), config.CACHE_TTL)
    
    @staticmethod
    def get_response(query: str, username: str = "") -> Optional[BotResponse]:
        key = f"response:v2:{CacheManager.hash_key(query)}:{username[:10]}"
        data = cache.get(key)
        if data:
            response = BotResponse(**data)
            response.cached = True
            return response
        return None
    
    @staticmethod
    def set_response(query: str, response: BotResponse, username: str = ""):
        key = f"response:v2:{CacheManager.hash_key(query)}:{username[:10]}"
        cache.set(key, asdict(response), config.CACHE_TTL)

# ============================================================================
# API Clients
# ============================================================================

class HuggingFaceClient:
    """HuggingFace Inference API client"""
    
    @staticmethod
    def _post(url: str, payload: dict, max_retries: int = 2) -> Optional[dict]:
        """Make HF API call with retry logic"""
        if not config.HF_API_KEY:
            return None
        
        headers = {
            "Authorization": f"Bearer {config.HF_API_KEY}",
            "Content-Type": "application/json"
        }
        
        for attempt in range(max_retries + 1):
            try:
                r = requests.post(url, headers=headers, json=payload, timeout=config.REQUEST_TIMEOUT)
                
                if r.status_code == 200:
                    return r.json()
                elif r.status_code == 503 and attempt < max_retries:
                    # Model loading, retry
                    time.sleep(2 ** attempt)
                    continue
                else:
                    logger.warning(f"HF API [{r.status_code}]: {r.text[:200]}")
            
            except requests.exceptions.Timeout:
                if attempt < max_retries:
                    time.sleep(1)
                else:
                    logger.error("HF API timeout")
            except Exception as e:
                logger.exception(f"HF API error: {e}")
                break
        
        return None
    
    @classmethod
    def similarity(cls, query: str, sentences: List[str]) -> Optional[List[float]]:
        """Get similarity scores for query against sentences"""
        payload = {
            "inputs": {
                "source_sentence": query,
                "sentences": sentences
            }
        }
        
        result = cls._post(config.HF_SIMILARITY_URL, payload)
        return result if isinstance(result, list) else None
    
    @classmethod
    def zero_shot(cls, text: str, labels: List[str]) -> Optional[Dict]:
        """Zero-shot classification"""
        payload = {
            "inputs": text,
            "parameters": {"candidate_labels": labels}
        }
        
        return cls._post(config.HF_ZERO_SHOT_URL, payload)

class OpenRouterClient:
    """OpenRouter API client for refinement"""
    
    @staticmethod
    def refine(query: str, answer: str) -> Optional[str]:
        """Refine answer to be more conversational"""
        if not config.OPENROUTER_KEY or not config.ENABLE_REFINEMENT:
            return None
        
        headers = {
            "Authorization": f"Bearer {config.OPENROUTER_KEY}",
            "Content-Type": "application/json"
        }
        
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a helpful assistant. Rephrase the answer to be natural, "
                    "conversational, and friendly while keeping all key information. "
                    "Keep it concise (2-3 sentences max). Don't add information not in the original."
                )
            },
            {
                "role": "user",
                "content": f"User asked: {query}\n\nAnswer: {answer}\n\nRephrase naturally:"
            }
        ]
        
        payload = {
            "model": config.OPENROUTER_MODEL,
            "messages": messages,
            "max_tokens": 200,
            "temperature": 0.3
        }
        
        try:
            r = requests.post(
                config.OPENROUTER_URL,
                headers=headers,
                json=payload,
                timeout=8
            )
            
            if r.status_code == 200:
                data = r.json()
                return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            else:
                logger.warning(f"OpenRouter [{r.status_code}]: {r.text[:200]}")
        
        except Exception as e:
            logger.warning(f"OpenRouter refinement failed: {e}")
        
        return None

# ============================================================================
# Intent Detection
# ============================================================================

class IntentDetector:
    """Fast local + HF zero-shot intent detection"""
    
    PATTERNS = {
        "greeting": {"hi", "hello", "hey", "morning", "evening", "good morning"},
        "thanks": {"thanks", "thank you", "ty", "thx", "appreciated"},
        "exit": {"no", "nothing", "nope", "bye", "exit"},
    }
    
    LABELS = ["greeting", "question", "thanks", "complaint", "urgent"]
    
    @classmethod
    def detect(cls, text: str) -> Intent:
        """Detect intent with caching"""
        # Check cache
        if cached := CacheManager.get_intent(text):
            return cached
        
        # Local pattern matching (fast)
        local = cls._local_detect(text)
        if local.confidence > 0.6:
            CacheManager.set_intent(text, local)
            return local
        
        # HuggingFace zero-shot (accurate)
        if config.HF_API_KEY:
            hf_intent = cls._hf_detect(text)
            if hf_intent:
                CacheManager.set_intent(text, hf_intent)
                return hf_intent
        
        # Fallback
        fallback = Intent("question", 0.3, "fallback")
        CacheManager.set_intent(text, fallback)
        return fallback
    
    @classmethod
    def _local_detect(cls, text: str) -> Intent:
        """Quick local pattern matching"""
        lower = text.lower().strip()
        length = len(text)
        
        # Single letter/number = selection
        if (length == 1 and text.isalpha()) or (text.isdigit() and length <= 2):
            return Intent("selection", 0.95, "local")
        
        # Pattern matching
        for intent_type, patterns in cls.PATTERNS.items():
            if length <= 50 and any(p in lower for p in patterns):
                return Intent(intent_type, 0.70, "local")
        
        return Intent("question", 0.3, "local")
    
    @classmethod
    def _hf_detect(cls, text: str) -> Optional[Intent]:
        """HuggingFace zero-shot classification"""
        result = HuggingFaceClient.zero_shot(text, cls.LABELS)
        
        if result and "labels" in result:
            return Intent(
                result["labels"][0],
                float(result["scores"][0]),
                "hf_zero_shot"
            )
        
        return None

# ============================================================================
# Semantic Search
# ============================================================================

class SemanticSearch:
    """Parallel chunked semantic search with HuggingFace"""
    
    @classmethod
    def search(cls, query: str) -> SearchResult:
        """Search KB with caching and exact match check"""
        # Check exact match first
        if exact_answer := kb.exact_match(query):
            return SearchResult(
                answer=exact_answer,
                score=1.0,
                question=query,
                method="exact_match"
            )
        
        # Check cache
        if cached := CacheManager.get_search(query):
            return cached
        
        # Perform semantic search
        if not config.HF_API_KEY:
            return SearchResult("", 0.0, "", "no_api")
        
        result = cls._parallel_search(query)
        
        # Cache good results
        if result.score >= config.SIMILARITY_MIN_SCORE:
            CacheManager.set_search(query, result)
        
        return result
    
    @classmethod
    def _parallel_search(cls, query: str) -> SearchResult:
        """Search all KB chunks in parallel"""
        questions = kb.get_questions()
        if not questions:
            return SearchResult("", 0.0, "", "no_kb")
        
        best_score = -1.0
        best_index = -1
        successful_chunks = 0
        
        # Process chunks in parallel
        with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as executor:
            futures = {}
            
            for offset in range(0, len(questions), config.CHUNK_SIZE):
                chunk = questions[offset:offset + config.CHUNK_SIZE]
                future = executor.submit(cls._search_chunk, query, chunk, offset)
                futures[future] = offset
            
            for future in as_completed(futures):
                try:
                    score, index = future.result()
                    if score > best_score:
                        best_score = score
                        best_index = index
                    successful_chunks += 1
                except Exception as e:
                    logger.error(f"Chunk search error: {e}")
        
        if best_index >= 0:
            return SearchResult(
                answer=kb.get_answer(best_index),
                score=best_score,
                question=questions[best_index],
                method="similarity",
                chunks_searched=successful_chunks
            )
        
        return SearchResult("", 0.0, "", "similarity_failed")
    
    @staticmethod
    def _search_chunk(query: str, chunk: List[str], offset: int) -> Tuple[float, int]:
        """Search a single chunk"""
        scores = HuggingFaceClient.similarity(query, chunk)
        
        if not scores:
            return -1.0, -1
        
        best_score = -1.0
        best_idx = -1
        
        for i, score in enumerate(scores):
            try:
                score = float(score)
                if score > best_score:
                    best_score = score
                    best_idx = offset + i
            except (ValueError, TypeError):
                continue
        
        return best_score, best_idx

# ============================================================================
# Session Manager
# ============================================================================

class SessionManager:
    """Multi-turn conversation state"""
    
    sessions: Dict[str, Dict] = {}
    
    @classmethod
    def is_awaiting_confirmation(cls, username: str) -> bool:
        return cls.sessions.get(username, {}).get("awaiting_confirmation", False)
    
    @classmethod
    def set_awaiting_confirmation(cls, username: str, value: bool):
        if username:
            cls.sessions.setdefault(username, {})
            cls.sessions[username]["awaiting_confirmation"] = value

# ============================================================================
# Bot Logic
# ============================================================================

class ChatBot:
    """Main bot orchestration with hybrid approach"""
    
    GREETING_PATTERNS = {"hi", "hello", "hey", "morning", "evening"}
    THANKS_PATTERNS = {"thanks", "thank you", "ty", "thx"}
    EXIT_PATTERNS = {"no", "nothing", "nope", "bye"}
    
    @classmethod
    def process_query(cls, query: str, username: str = "") -> BotResponse:
        """Main entry point"""
        start_time = time.time()
        
        # Check full response cache
        if cached := CacheManager.get_response(query, username):
            logger.info(f"✓ Full cache hit: {query[:50]}")
            return cached
        
        # Handle exit intent
        if username and SessionManager.is_awaiting_confirmation(username):
            if any(word in query.lower() for word in cls.EXIT_PATTERNS):
                SessionManager.set_awaiting_confirmation(username, False)
                return cls._create_response(
                    f"Alright {username}, glad I could help! Have a great day! 🚀",
                    1.0,
                    Intent("exit", 1.0, "session"),
                    "exit_intent",
                    {}
                )
            SessionManager.set_awaiting_confirmation(username, False)
        
        # Detect intent
        intent = IntentDetector.detect(query)
        
        # Handle special intents
        if intent.label == "greeting":
            greeting = f"Hi {username}! 👋 How can I help you today?" if username else "Hi! 👋 How can I help?"
            return cls._create_response(greeting, 1.0, intent, "greeting", {})
        
        if intent.label == "thanks":
            if username:
                SessionManager.set_awaiting_confirmation(username, True)
            thanks = f"You're welcome, {username}! Anything else I can help with?" if username else "You're welcome! Anything else?"
            return cls._create_response(thanks, 1.0, intent, "thanks", {})
        
        # Semantic search (HuggingFace)
        search_result = SemanticSearch.search(query)
        
        # Check if we found a good match
        if not search_result.answer or search_result.score < config.SIMILARITY_MIN_SCORE:
            fallback = cls._get_fallback(username)
            response = cls._create_response(
                fallback,
                search_result.score,
                intent,
                "fallback",
                {"search_method": search_result.method}
            )
            CacheManager.set_response(query, response, username)
            return response
        
        # Determine if we should refine
        final_answer = search_result.answer
        method = search_result.method
        
        should_refine = (
            config.ENABLE_REFINEMENT and
            config.OPENROUTER_KEY and
            (not config.REFINE_HIGH_CONFIDENCE_ONLY or 
             search_result.score >= config.HIGH_CONFIDENCE_SCORE)
        )
        
        if should_refine:
            if refined := OpenRouterClient.refine(query, search_result.answer):
                final_answer = refined
                method = f"refined_{method}"
        
        duration = int((time.time() - start_time) * 1000)
        
        response = cls._create_response(
            final_answer,
            search_result.score,
            intent,
            method,
            {
                "question_matched": search_result.question,
                "chunks_searched": search_result.chunks_searched,
                "response_time_ms": duration,
                "was_refined": "refined" in method
            }
        )
        
        # Cache the full response
        CacheManager.set_response(query, response, username)
        
        return response
    
    @staticmethod
    def _get_fallback(username: str = "") -> str:
        base = (
            "I couldn't find a specific answer. I can help with:\n"
            "• Managing Clients and Services\n"
            "• Bookings and Appointments\n"
            "• Documents and Reports\n"
            "• Account Management\n\n"
            "What would you like to know?"
        )
        return f"Hi {username}, {base}" if username else base
    
    @staticmethod
    def _create_response(
        answer: str,
        confidence: float,
        intent: Intent,
        method: str,
        metadata: Dict
    ) -> BotResponse:
        return BotResponse(
            answer=answer,
            confidence=confidence,
            intent=intent,
            method=method,
            metadata=metadata,
            cached=False
        )

# ============================================================================
# API Endpoints
# ============================================================================

@csrf_exempt
def bot_query(request):
    """
    Main bot endpoint
    POST /api/bot/query/
    Body: {"text": "your question", "username": "optional"}
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    
    query = (data.get("text") or data.get("message") or "").strip()
    username = (data.get("username") or "").strip()
    
    if not query:
        return JsonResponse({"error": "No query provided"}, status=400)
    
    if len(query) > 1000:
        return JsonResponse({"error": "Query too long"}, status=400)
    
    try:
        response = ChatBot.process_query(query, username)
        
        return JsonResponse({
            "ok": True,
            "answer": response.answer,
            "confidence": response.confidence,
            "intent": {
                "label": response.intent.label,
                "confidence": response.intent.confidence,
                "method": response.intent.method
            },
            "method": response.method,
            "cached": response.cached,
            "metadata": response.metadata
        })
    
    except Exception as e:
        logger.exception(f"Bot error: {e}")
        return JsonResponse({
            "ok": False,
            "error": "Processing error",
            "answer": "I encountered an error. Please try again."
        }, status=500)


@csrf_exempt
def bot_health(request):
    """Health check endpoint"""
    cache_ok = False
    try:
        cache.set('health', 'ok', 10)
        cache_ok = cache.get('health') == 'ok'
    except:
        pass
    
    return JsonResponse({
        "status": "healthy" if (cache_ok and config.HF_API_KEY) else "degraded",
        "components": {
            "cache": "ok" if cache_ok else "unavailable",
            "huggingface": "configured" if config.HF_API_KEY else "missing",
            "openrouter": "configured" if config.OPENROUTER_KEY else "missing",
            "kb": "loaded" if kb.qa_pairs else "empty"
        },
        "metrics": {
            "kb_entries": len(kb.qa_pairs),
            "refinement_enabled": config.ENABLE_REFINEMENT,
            "similarity_threshold": config.SIMILARITY_MIN_SCORE
        }
    })


@csrf_exempt
def clear_session(request):
    """
    Clear user session history
    POST /api/bot/clear-session/
    Body: {"username": "john_doe"}
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    
    try:
        data = json.loads(request.body)
        username = data.get("username")
        
        if username and username in SessionManager.sessions:
            del SessionManager.sessions[username]
            logger.info(f"Cleared session for user: {username}")
            return JsonResponse({"ok": True, "message": "Session cleared"})
        
        return JsonResponse({"ok": True, "message": "No session found"})
    
    except Exception as e:
        logger.error(f"Clear session error: {e}")
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
def get_conversation_history(request):
    """
    Get user's recent conversation history (optional - for server-side storage)
    GET /api/bot/history/?username=john_doe
    """
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    
    username = request.GET.get("username", "")
    
    if not username:
        return JsonResponse({"error": "Username required"}, status=400)
    
    # Get session history if exists
    session = SessionManager.sessions.get(username, {})
    history = session.get("history", [])
    
    return JsonResponse({
        "ok": True,
        "username": username,
        "history": history[-20:],  # Last 20 messages
        "count": len(history)
    })


@csrf_exempt
def clear_cache(request):
    """
    Clear all bot caches (Redis)
    POST /api/bot/clear-cache/
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    
    try:
        # Clear all bot-related cache keys
        cache_keys = [
            'int:v2:*',  # Intent cache
            'search:v2:*',  # Search cache
            'response:v2:*',  # Response cache
        ]
        
        cleared = 0
        for pattern in cache_keys:
            try:
                # For Redis backend
                if hasattr(cache, 'delete_pattern'):
                    cleared += cache.delete_pattern(pattern)
                else:
                    # For other backends, clear all
                    cache.clear()
                    cleared = 1
                    break
            except Exception as e:
                logger.warning(f"Failed to clear pattern {pattern}: {e}")
        
        logger.info(f"Cleared {cleared} cache entries")
        
        return JsonResponse({
            "ok": True,
            "message": "Cache cleared successfully",
            "entries_cleared": cleared
        })
    
    except Exception as e:
        logger.error(f"Cache clear error: {e}")
        return JsonResponse({
            "ok": False,
            "error": "Failed to clear cache"
        }, status=500)