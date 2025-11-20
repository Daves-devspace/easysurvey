# apps/EasyDocs/views_bot.py
"""
Production Smart Chatbot - Maximum Intelligence
- Context-aware conversation tracking
- Smart intent detection with question structure analysis
- Confidence-based clarification requests
- New user onboarding
- Repeat question detection
- Multi-intent handling
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
    CHUNK_SIZE = 40
    MAX_WORKERS = 4
    REQUEST_TIMEOUT = 12
    CACHE_TTL = 3600
    
    # Intelligence Thresholds
    SIMILARITY_MIN_SCORE = 0.30  # Absolute minimum
    MEDIUM_CONFIDENCE = 0.60  # Ask for clarification below this
    HIGH_CONFIDENCE_SCORE = 0.75  # Refine above this
    
    # Features
    ENABLE_REFINEMENT = True
    REFINE_HIGH_CONFIDENCE_ONLY = True
    ENABLE_CLARIFICATION = True  # Ask questions when unsure
    ENABLE_ONBOARDING = True  # Detect new users

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
                    normalized = ' '.join(q.lower().split())
                    self.exact_match_dict[normalized] = a
            
            logger.info(f"✓ Loaded {len(self.qa_pairs)} KB entries")
        except Exception as e:
            logger.exception(f"Failed to load KB: {e}")
    
    def exact_match(self, query: str) -> Optional[str]:
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
    @staticmethod
    def hash_key(text: str) -> str:
        return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]
    
    @staticmethod
    def get_intent(text: str) -> Optional[Intent]:
        key = f"int:v3:{CacheManager.hash_key(text)}"
        data = cache.get(key)
        return Intent(**data) if data else None
    
    @staticmethod
    def set_intent(text: str, intent: Intent):
        key = f"int:v3:{CacheManager.hash_key(text)}"
        cache.set(key, asdict(intent), config.CACHE_TTL)
    
    @staticmethod
    def get_search(text: str) -> Optional[SearchResult]:
        key = f"search:v3:{CacheManager.hash_key(text)}"
        data = cache.get(key)
        return SearchResult(**data) if data else None
    
    @staticmethod
    def set_search(text: str, result: SearchResult):
        key = f"search:v3:{CacheManager.hash_key(text)}"
        cache.set(key, asdict(result), config.CACHE_TTL)
    
    @staticmethod
    def get_response(query: str, username: str = "") -> Optional[BotResponse]:
        key = f"response:v3:{CacheManager.hash_key(query)}:{username[:10]}"
        data = cache.get(key)
        if data:
            response = BotResponse(**data)
            response.cached = True
            return response
        return None
    
    @staticmethod
    def set_response(query: str, response: BotResponse, username: str = ""):
        key = f"response:v3:{CacheManager.hash_key(query)}:{username[:10]}"
        cache.set(key, asdict(response), config.CACHE_TTL)

# ============================================================================
# API Clients (HuggingFace & OpenRouter)
# ============================================================================

class HuggingFaceClient:
    @staticmethod
    def _post(url: str, payload: dict, max_retries: int = 2) -> Optional[dict]:
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
        payload = {"inputs": {"source_sentence": query, "sentences": sentences}}
        result = cls._post(config.HF_SIMILARITY_URL, payload)
        return result if isinstance(result, list) else None
    
    @classmethod
    def zero_shot(cls, text: str, labels: List[str]) -> Optional[Dict]:
        payload = {"inputs": text, "parameters": {"candidate_labels": labels}}
        return cls._post(config.HF_ZERO_SHOT_URL, payload)

class OpenRouterClient:
    @staticmethod
    def refine(query: str, answer: str) -> Optional[str]:
        if not config.OPENROUTER_KEY or not config.ENABLE_REFINEMENT:
            return None
        
        headers = {
            "Authorization": f"Bearer {config.OPENROUTER_KEY}",
            "Content-Type": "application/json"
        }
        
        messages = [
            {
                "role": "system",
                "content": "Rephrase the answer naturally and conversationally while keeping all key information. Be concise (2-3 sentences). Don't add new info."
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
            r = requests.post(config.OPENROUTER_URL, headers=headers, json=payload, timeout=8)
            if r.status_code == 200:
                data = r.json()
                return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            else:
                logger.warning(f"OpenRouter [{r.status_code}]: {r.text[:200]}")
        except Exception as e:
            logger.warning(f"OpenRouter failed: {e}")
        return None

# ============================================================================
# Smart Intent Detection
# ============================================================================

class IntentDetector:
    """Intelligent intent detection with question structure analysis"""
    
    PATTERNS = {
        "greeting": {"hi", "hello", "hey", "morning", "evening", "good morning", "afternoon", "sup", "yo"},
        "thanks": {"thanks", "thank you", "ty", "thx", "appreciated", "cheers", "thank"},
        "exit": {"no", "nothing", "nope", "bye", "exit", "quit", "stop"},
        "help_request": {"help", "stuck", "confused", "lost", "don't understand", "don't know"},
        "new_user": {"new to", "first time", "getting started", "where to start", "how to start", "just started"},
        "what_else": {"what else", "anything else", "what more", "what other", "else can you"},
    }
    
    QUESTION_STARTERS = ["how", "what", "where", "when", "why", "who", "which", "can", "could", "would", "should", "do", "does", "is", "are", "am"]
    
    TYPO_MAP = {
        "clint": "client", "cilent": "client", "cient": "client",
        "servce": "service", "serivce": "service",
        "bocking": "booking", "bokking": "booking",
        "documnet": "document", "docment": "document",
        "calender": "calendar", "calandar": "calendar",
    }
    
    @classmethod
    def detect(cls, text: str, context: Optional[Dict] = None) -> Intent:
        """Smart intent detection with context"""
        corrected_text = cls._fix_typos(text)
        lower = corrected_text.lower().strip()
        
        # Cache check
        cache_key = f"{corrected_text}:{context.get('last_topic', '') if context else ''}"
        if cached := CacheManager.get_intent(cache_key):
            return cached
        
        # Check if it's a question structure
        if cls._is_question_structure(lower):
            # It's definitely a question, not a greeting
            intent = cls._classify_question(lower, context)
            CacheManager.set_intent(cache_key, intent)
            return intent
        
        # Check for specific patterns
        local = cls._local_detect(lower, context)
        if local.confidence > 0.7:
            CacheManager.set_intent(cache_key, local)
            return local
        
        # HuggingFace zero-shot
        if config.HF_API_KEY and len(text.split()) > 2:
            hf_intent = cls._hf_detect(corrected_text)
            if hf_intent:
                CacheManager.set_intent(cache_key, hf_intent)
                return hf_intent
        
        # Fallback
        fallback = Intent("question", 0.3, "fallback")
        CacheManager.set_intent(cache_key, fallback)
        return fallback
    
    @classmethod
    def _is_question_structure(cls, text: str) -> bool:
        """Detect question structure"""
        # Starts with question word
        if any(text.startswith(qw + " ") for qw in cls.QUESTION_STARTERS):
            return True
        # Contains question mark
        if "?" in text:
            return True
        # Contains question words in middle
        words = text.split()
        if len(words) > 1 and any(qw in words[:3] for qw in cls.QUESTION_STARTERS):
            return True
        return False
    
    @classmethod
    def _classify_question(cls, text: str, context: Optional[Dict]) -> Intent:
        """Classify question type"""
        # New user questions
        if any(p in text for p in cls.PATTERNS["new_user"]):
            return Intent("new_user", 0.95, "pattern")
        
        # What else questions
        if any(p in text for p in cls.PATTERNS["what_else"]):
            return Intent("what_else", 0.90, "pattern")
        
        # Help requests
        if any(p in text for p in cls.PATTERNS["help_request"]):
            return Intent("help_request", 0.85, "pattern")
        
        # Follow-up question (uses pronouns referring to previous topic)
        if context and context.get("last_topic"):
            pronouns = ["it", "they", "them", "that", "this", "these", "those"]
            if any(text.startswith(p + " ") or text.startswith(p + "?") for p in pronouns):
                return Intent("follow_up_question", 0.80, "context")
        
        # General question
        return Intent("question", 0.75, "structure")
    
    @classmethod
    def _local_detect(cls, text: str, context: Optional[Dict]) -> Intent:
        """Local pattern matching"""
        words = text.split()
        length = len(text)
        
        # Single letter/number
        if (length == 1 and text.isalpha()) or (text.isdigit() and length <= 2):
            return Intent("selection", 0.95, "local")
        
        # Very short greetings (1-2 words, no question structure)
        if len(words) <= 2 and not cls._is_question_structure(text):
            for intent_type, patterns in cls.PATTERNS.items():
                if intent_type in ["greeting", "thanks", "exit"]:
                    if any(p in text for p in patterns):
                        return Intent(intent_type, 0.85, "local")
        
        # Pattern matching for all other types
        for intent_type, patterns in cls.PATTERNS.items():
            if any(p in text for p in patterns):
                confidence = 0.70
                matches = sum(1 for p in patterns if p in text)
                if matches > 1:
                    confidence = 0.85
                return Intent(intent_type, confidence, "local")
        
        return Intent("question", 0.3, "local")
    
    @classmethod
    def _fix_typos(cls, text: str) -> str:
        words = text.lower().split()
        corrected = [cls.TYPO_MAP.get(word, word) for word in words]
        return " ".join(corrected)
    
    @classmethod
    def _hf_detect(cls, text: str) -> Optional[Intent]:
        labels = ["greeting", "question", "thanks", "complaint", "urgent", "request"]
        result = HuggingFaceClient.zero_shot(text, labels)
        if result and "labels" in result:
            return Intent(result["labels"][0], float(result["scores"][0]), "hf_zero_shot")
        return None

# ============================================================================
# Semantic Search
# ============================================================================

class SemanticSearch:
    @classmethod
    def search(cls, query: str) -> SearchResult:
        if exact_answer := kb.exact_match(query):
            return SearchResult(exact_answer, 1.0, query, "exact_match")
        
        if cached := CacheManager.get_search(query):
            return cached
        
        if not config.HF_API_KEY:
            return SearchResult("", 0.0, "", "no_api")
        
        result = cls._parallel_search(query)
        if result.score >= config.SIMILARITY_MIN_SCORE:
            CacheManager.set_search(query, result)
        return result
    
    @classmethod
    def _parallel_search(cls, query: str) -> SearchResult:
        questions = kb.get_questions()
        if not questions:
            return SearchResult("", 0.0, "", "no_kb")
        
        best_score, best_index, successful_chunks = -1.0, -1, 0
        
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
                        best_score, best_index = score, index
                    successful_chunks += 1
                except Exception as e:
                    logger.error(f"Chunk search error: {e}")
        
        if best_index >= 0:
            return SearchResult(
                kb.get_answer(best_index),
                best_score,
                questions[best_index],
                "similarity",
                successful_chunks
            )
        return SearchResult("", 0.0, "", "similarity_failed")
    
    @staticmethod
    def _search_chunk(query: str, chunk: List[str], offset: int) -> Tuple[float, int]:
        scores = HuggingFaceClient.similarity(query, chunk)
        if not scores:
            return -1.0, -1
        
        best_score, best_idx = -1.0, -1
        for i, score in enumerate(scores):
            try:
                score = float(score)
                if score > best_score:
                    best_score, best_idx = score, offset + i
            except (ValueError, TypeError):
                continue
        return best_score, best_idx

# ============================================================================
# Smart Session Manager
# ============================================================================

class SessionManager:
    """Advanced conversation tracking"""
    sessions: Dict[str, Dict] = {}
    
    @classmethod
    def get_session(cls, username: str) -> Dict:
        if username not in cls.sessions:
            cls.sessions[username] = {
                "awaiting_confirmation": False,
                "last_query": "",
                "last_intent": "",
                "last_topic": "",
                "last_answer": "",
                "conversation_history": [],
                "asked_questions": set()
            }
        return cls.sessions[username]
    
    @classmethod
    def update_context(cls, username: str, query: str, intent: str, topic: str, answer: str):
        session = cls.get_session(username)
        session["last_query"] = query
        session["last_intent"] = intent
        session["last_topic"] = topic
        session["last_answer"] = answer
        session["asked_questions"].add(query.lower().strip())
        
        session["conversation_history"].append({
            "query": query,
            "intent": intent,
            "topic": topic,
            "answer": answer[:100],
            "timestamp": time.time()
        })
        
        if len(session["conversation_history"]) > 10:
            session["conversation_history"] = session["conversation_history"][-10:]
    
    @classmethod
    def is_repeat_question(cls, username: str, query: str) -> bool:
        session = cls.get_session(username)
        normalized = query.lower().strip()
        return normalized in session.get("asked_questions", set())
    
    @classmethod
    def get_context(cls, username: str) -> Dict:
        session = cls.get_session(username)
        return {
            "last_query": session.get("last_query", ""),
            "last_intent": session.get("last_intent", ""),
            "last_topic": session.get("last_topic", ""),
            "last_answer": session.get("last_answer", "")
        }
    
    @classmethod
    def is_awaiting_confirmation(cls, username: str) -> bool:
        return cls.get_session(username).get("awaiting_confirmation", False)
    
    @classmethod
    def set_awaiting_confirmation(cls, username: str, value: bool):
        cls.get_session(username)["awaiting_confirmation"] = value

# ============================================================================
# Smart ChatBot with Full Intelligence
# ============================================================================

class ChatBot:
    """Maximum intelligence chatbot"""
    
    @classmethod
    def process_query(cls, query: str, username: str = "") -> BotResponse:
        start_time = time.time()
        
        # Check cache
        if cached := CacheManager.get_response(query, username):
            logger.info(f"✓ Cache hit: {query[:50]}")
            return cached
        
        # Get conversation context
        context = SessionManager.get_context(username) if username else {}
        
        # Detect intent with context
        intent = IntentDetector.detect(query, context)
        
        # Handle exit
        if username and SessionManager.is_awaiting_confirmation(username):
            if intent.label == "exit":
                SessionManager.set_awaiting_confirmation(username, False)
                return cls._create_response(
                    f"Alright {username}, glad I could help!",
                    1.0, intent, "exit_intent", {}
                )
            SessionManager.set_awaiting_confirmation(username, False)
        
        # Handle greetings
        if intent.label == "greeting":
            greeting = f"Hi {username}! How can I help you today?" if username else "Hi! How can I help?"
            return cls._create_response(greeting, 1.0, intent, "greeting", {})
        
        # Handle thanks
        if intent.label == "thanks":
            if username:
                SessionManager.set_awaiting_confirmation(username, True)
            thanks = f"You're welcome, {username}! Anything else I can help with?" if username else "You're welcome!"
            return cls._create_response(thanks, 1.0, intent, "thanks", {})
        
        # Handle new user
        if intent.label == "new_user":
            return cls._handle_new_user(username, intent)
        
        # Handle "what else"
        if intent.label == "what_else":
            return cls._handle_what_else(username, context, intent)
        
        # Handle help requests
        if intent.label == "help_request":
            return cls._handle_help_request(query, username, intent)
        
        # Check for repeat question
        if username and SessionManager.is_repeat_question(username, query):
            return cls._handle_repeat_question(context, intent)
        
        # CRITICAL: Check for pronouns/references before regular search
        has_pronouns = cls._has_contextual_pronouns(query)
        last_topic = context.get("last_topic", "")
        
        if has_pronouns and last_topic:
            # User is referring to previous topic - use context automatically
            logger.info(f"Contextual query detected: '{query}' about '{last_topic}'")
            return cls._handle_contextual_query(query, username, last_topic, context, intent)
        
        # Handle follow-up questions (explicit follow-ups)
        if intent.label == "follow_up_question" and last_topic:
            return cls._handle_followup(query, username, context, intent)
        
        # Regular semantic search
        search_result = SemanticSearch.search(query)
        topic = cls._extract_topic(query)
        
        # Check confidence and decide
        if not search_result.answer or search_result.score < config.SIMILARITY_MIN_SCORE:
            fallback = cls._smart_fallback(query, username, intent)
            response = cls._create_response(fallback, search_result.score, intent, "smart_fallback", {})
            CacheManager.set_response(query, response, username)
            return response
        
        # Medium confidence - ONLY ask for clarification if NO clear topic context
        if config.ENABLE_CLARIFICATION and search_result.score < config.MEDIUM_CONFIDENCE:
            # If user was just talking about a topic, DON'T ask for clarification
            if not (last_topic and cls._query_relates_to_topic(query, last_topic)):
                clarification = cls._ask_clarification(query, search_result, topic)
                response = cls._create_response(
                    clarification, search_result.score, intent, "clarification_request",
                    {"matched_question": search_result.question}
                )
                return response
            # If query relates to last topic, continue with answer
            logger.info(f"Medium confidence but relates to topic '{last_topic}' - answering directly")
        
        # High confidence - provide answer
        final_answer = search_result.answer
        method = search_result.method
        
        # Refine if very high confidence
        if (config.ENABLE_REFINEMENT and config.OPENROUTER_KEY and 
            search_result.score >= config.HIGH_CONFIDENCE_SCORE):
            if refined := OpenRouterClient.refine(query, search_result.answer):
                final_answer = refined
                method = f"refined_{method}"
        
        # Update context
        if username:
            SessionManager.update_context(username, query, intent.label, topic or last_topic, final_answer)
        
        duration = int((time.time() - start_time) * 1000)
        
        response = cls._create_response(
            final_answer, search_result.score, intent, method,
            {
                "question_matched": search_result.question,
                "response_time_ms": duration,
                "was_refined": "refined" in method,
                "topic": topic or last_topic
            }
        )
        
        CacheManager.set_response(query, response, username)
        return response
    
    @classmethod
    def _has_contextual_pronouns(cls, query: str) -> bool:
        """Check if query contains pronouns that refer to previous context"""
        lower = query.lower().strip()
        
        # Pronouns that typically refer to previous context
        contextual_pronouns = [
            # At start of sentence
            "they ", "them ", "it ", "that ", "this ", "these ", "those ",
            # Questions starting with pronouns
            "are they", "is it", "does it", "do they", "can they", "will they",
            "how are they", "how is it", "how does it", "how do they",
            "what are they", "what is it", "why are they", "why is it",
            "where are they", "where is it"
        ]
        
        return any(lower.startswith(pron) or f" {pron}" in f" {lower}" for pron in contextual_pronouns)
    
    @classmethod
    def _query_relates_to_topic(cls, query: str, topic: str) -> bool:
        """Check if query semantically relates to the last topic"""
        lower = query.lower()
        
        # If query explicitly mentions the topic
        if topic in lower:
            return True
        
        # Topic-related keywords
        topic_keywords = {
            "document": ["stored", "saved", "secure", "security", "safe", "protected", "access", "shared", "sharing"],
            "client": ["information", "details", "contact", "manage", "edit", "delete", "view"],
            "service": ["workflow", "process", "status", "complete", "progress"],
            "booking": ["scheduled", "appointment", "surveyor", "assign", "calendar", "date"],
            "system": ["feature", "capability", "effective", "work", "function", "use"]
        }
        
        keywords = topic_keywords.get(topic, [])
        return any(keyword in lower for keyword in keywords)
    
    @classmethod
    def _handle_contextual_query(cls, query: str, username: str, topic: str, context: Dict, intent: Intent) -> BotResponse:
        """Handle queries with pronouns referring to previous topic"""
        logger.info(f"Handling contextual query about '{topic}'")
        
        # Replace pronouns with topic for better search
        contextual_query = cls._expand_query_with_context(query, topic)
        logger.info(f"Expanded query: '{contextual_query}'")
        
        # Search with expanded query
        search_result = SemanticSearch.search(contextual_query)
        
        # If good match found
        if search_result.score >= config.SIMILARITY_MIN_SCORE:
            final_answer = search_result.answer
            
            # Refine if confidence is high enough
            if config.ENABLE_REFINEMENT and config.OPENROUTER_KEY and search_result.score >= 0.60:
                if refined := OpenRouterClient.refine(query, search_result.answer):
                    final_answer = refined
            
            # Update context
            SessionManager.update_context(username, query, intent.label, topic, final_answer)
            
            return cls._create_response(
                final_answer,
                search_result.score,
                intent,
                "context_aware_answer",
                {
                    "original_query": query,
                    "expanded_query": contextual_query,
                    "topic": topic,
                    "matched_question": search_result.question
                }
            )
        
        # If no good match with context, provide topic-specific fallback
        fallback = cls._contextual_fallback(query, topic)
        return cls._create_response(
            fallback,
            search_result.score,
            intent,
            "contextual_fallback",
            {"topic": topic, "original_query": query}
        )
    
    @classmethod
    def _expand_query_with_context(cls, query: str, topic: str) -> str:
        """Expand query by replacing pronouns with topic"""
        lower = query.lower()
        
        # Replace pronouns with topic
        replacements = {
            "they": topic + "s",
            "them": topic + "s",
            "it": topic,
            "that": topic,
            "this": topic,
            "these": topic + "s",
            "those": topic + "s"
        }
        
        words = lower.split()
        expanded_words = []
        
        for word in words:
            # Remove punctuation for matching
            clean_word = word.strip("?.!,")
            if clean_word in replacements:
                expanded_words.append(replacements[clean_word])
            else:
                expanded_words.append(word)
        
        expanded = " ".join(expanded_words)
        
        # Ensure topic is in the query
        if topic not in expanded:
            expanded = f"{topic} {expanded}"
        
        return expanded
    
    @classmethod
    def _contextual_fallback(cls, query: str, topic: str) -> str:
        """Provide fallback based on topic context"""
        fallbacks = {
            "document": (
                f"I'm not sure about that specific aspect of documents. "
                f"I can help with:\n"
                f"• How documents are stored\n"
                f"• Document security\n"
                f"• How to upload documents\n"
                f"• How documents are shared\n\n"
                f"Which one would help?"
            ),
            "client": (
                f"I'm not sure about that specific client question. "
                f"I can help with:\n"
                f"• Adding clients\n"
                f"• Viewing client details\n"
                f"• Editing client information\n"
                f"• Managing client services\n\n"
                f"What would you like to know?"
            ),
            "service": (
                f"I'm not sure about that service question. "
                f"I can help with:\n"
                f"• Adding services\n"
                f"• Service workflows\n"
                f"• Checking service status\n"
                f"• Service types\n\n"
                f"Which interests you?"
            ),
            "system": (
                f"I'm not sure about that system aspect. "
                f"I can help explain:\n"
                f"• System features\n"
                f"• How it works\n"
                f"• What it can do\n"
                f"• Getting started\n\n"
                f"What would you like to know?"
            )
        }
        
        return fallbacks.get(topic, f"I'm not sure about that regarding {topic}. Could you be more specific?")

    
    @classmethod
    def _handle_new_user(cls, username: str, intent: Intent) -> BotResponse:
        """Onboarding for new users"""
        welcome = (
            f"Welcome to the system, {username}! 🎉\n\n"
            "Here's how to get started:\n\n"
            "**1. Add Your First Client**\n"
            "Go to Clients → Add Client, fill in their details\n\n"
            "**2. Create Services**\n"
            "Attach title deeds or ground services to your client\n\n"
            "**3. Book Field Work**\n"
            "Use the calendar to schedule surveys\n\n"
            "**4. Track Everything**\n"
            "View reports, manage accounts, upload documents\n\n"
            "Which step would you like help with first?"
        )
        return cls._create_response(welcome, 1.0, intent, "onboarding", {})
    
    @classmethod
    def _handle_what_else(cls, username: str, context: Dict, intent: Intent) -> BotResponse:
        """Handle 'what else can you help with' questions"""
        last_topic = context.get("last_topic", "")
        
        if last_topic == "client":
            response = f"Besides clients, I can help you with:\n• **Services** - Add title deeds or ground services\n• **Bookings** - Schedule field work and surveys\n• **Documents** - Upload and manage files\n• **Accounts** - Track payments and expenses\n\nWhat would you like to know about?"
        elif last_topic == "service":
            response = f"Besides services, I can help with:\n• **Clients** - Add and manage clients\n• **Bookings** - Schedule appointments\n• **Documents** - File management\n• **Accounts** - Financial tracking\n\nWhich one interests you?"
        else:
            response = f"I can help you with many things, {username}!\n• **Clients** - Add, view, edit clients\n• **Services** - Title deeds and ground services\n• **Bookings** - Schedule field work\n• **Documents** - Upload and share files\n• **Accounts** - Track finances\n• **Staff** - Manage employees\n\nWhat would you like to explore?"
        
        return cls._create_response(response, 0.95, intent, "what_else_response", {"last_topic": last_topic})
    
    @classmethod
    def _handle_help_request(cls, query: str, username: str, intent: Intent) -> BotResponse:
        """Handle stuck/confused queries"""
        lower = query.lower()
        
        if "client" in lower:
            help_text = f"I can help with clients! Are you trying to:\n• **Add** a new client?\n• **View** client details?\n• **Edit** client information?\n• **Delete** a client?\n\nWhich one?"
        elif "service" in lower:
            help_text = f"I can help with services! Are you trying to:\n• **Add** a service to a client?\n• **View** services?\n• **Edit** a service?\n• **Check** service status?\n\nLet me know!"
        elif "booking" in lower:
            help_text = f"I can help with bookings! Are you trying to:\n• **Create** a new booking?\n• **View** bookings?\n• **Assign** surveyors?\n• **Check** booking status?\n\nWhat do you need?"
        else:
            help_text = f"I'm here to help, {username}! What are you trying to do?\n\nI can assist with:\n• **Clients** - Managing client information\n• **Services** - Title deeds and ground work\n• **Bookings** - Scheduling field work\n• **Documents** - File management\n• **Accounts** - Financial tracking\n\nJust tell me what you need!"
        
        return cls._create_response(help_text, 0.95, intent, "help_response", {})
    
    @classmethod
    def _handle_repeat_question(cls, context: Dict, intent: Intent) -> BotResponse:
        """Handle repeated questions"""
        last_answer = context.get("last_answer", "")
        
        response = (
            "I just answered that! Would you like:\n"
            "• **More details** on a specific part?\n"
            "• **A different explanation**?\n"
            "• **Help with something else**?\n\n"
            "Let me know what would be helpful!"
        )
        
        if last_answer:
            response = f"I explained that earlier. {response}"
        
        return cls._create_response(response, 0.90, intent, "repeat_detection", {})
    
    @classmethod
    def _handle_followup(cls, query: str, username: str, context: Dict, intent: Intent) -> BotResponse:
        """Handle follow-up questions with context"""
        last_topic = context.get("last_topic", "")
        
        # Search with context
        contextual_query = f"{last_topic} {query}"
        search_result = SemanticSearch.search(contextual_query)
        
        if search_result.score >= config.SIMILARITY_MIN_SCORE:
            final_answer = search_result.answer
            
            if config.ENABLE_REFINEMENT and config.OPENROUTER_KEY:
                if refined := OpenRouterClient.refine(query, search_result.answer):
                    final_answer = refined
            
            # Update context
            SessionManager.update_context(username, query, intent.label, last_topic, final_answer)
            
            return cls._create_response(
                final_answer, search_result.score, intent, "context_aware_search",
                {"previous_topic": last_topic, "contextual_query": contextual_query}
            )
        
        # Couldn't find with context
        return cls._create_response(
            f"I'm not sure about that regarding {last_topic}. Could you be more specific?",
            search_result.score, intent, "followup_clarification",
            {"topic": last_topic}
        )
    
    @classmethod
    def _ask_clarification(cls, query: str, search_result: SearchResult, topic: str) -> str:
        """Ask for clarification when confidence is medium"""
        matched_question = search_result.question
        
        # Generate alternatives based on topic
        alternatives = cls._generate_alternatives(topic, query)
        
        clarification = (
            f"I found something about **{topic or 'that'}**, but want to make sure I understand correctly.\n\n"
            f"Are you asking about:\n"
        )
        
        if alternatives:
            for i, alt in enumerate(alternatives[:3], 1):
                clarification += f"{i}. {alt}\n"
        else:
            clarification += f"• {matched_question}\n"
        
        clarification += "\nWhich one matches your question?"
        
        return clarification
    
    @classmethod
    def _generate_alternatives(cls, topic: str, query: str) -> List[str]:
        """Generate alternative interpretations"""
        alternatives = []
        
        if topic == "client":
            alternatives = [
                "Adding a new client to the system?",
                "Viewing existing client information?",
                "Editing client details?",
                "Deleting or managing clients?"
            ]
        elif topic == "service":
            alternatives = [
                "Adding a service to a client?",
                "Viewing service status?",
                "Understanding service types?",
                "Managing service workflows?"
            ]
        elif topic == "booking":
            alternatives = [
                "Creating a new booking?",
                "Viewing the calendar?",
                "Assigning surveyors?",
                "Checking booking status?"
            ]
        elif topic == "document":
            alternatives = [
                "Uploading a document?",
                "Viewing or downloading documents?",
                "Sharing documents?",
                "Document security and permissions?"
            ]
        
        return alternatives
    
    @classmethod
    def _extract_topic(cls, query: str) -> str:
        """Extract main topic from query"""
        lower = query.lower()
        
        topics = {
            "client": ["client", "customer"],
            "service": ["service", "title deed", "ground", "deed"],
            "booking": ["booking", "appointment", "survey", "calendar"],
            "document": ["document", "doc", "file", "pdf", "upload"],
            "account": ["account", "payment", "cashbook", "invoice", "expense"],
            "employee": ["employee", "staff", "user", "surveyor", "team"],
            "system": ["system", "platform", "software", "application"]
        }
        
        for topic, keywords in topics.items():
            if any(keyword in lower for keyword in keywords):
                return topic
        return ""
    
    @classmethod
    def _smart_fallback(cls, query: str, username: str, intent: Intent) -> str:
        """Intelligent fallback based on query content"""
        lower = query.lower()
        
        # Topic-specific fallbacks
        if any(word in lower for word in ["client", "customer"]):
            return (
                f"I can help with clients! Try asking:\n"
                "• 'How do I add a client?'\n"
                "• 'How to view client details?'\n"
                "• 'How to edit client information?'\n\n"
                "What would you like to know?"
            )
        
        if any(word in lower for word in ["service", "title", "deed"]):
            return (
                f"I can help with services! Try asking:\n"
                "• 'How do I add a service?'\n"
                "• 'What are the service types?'\n"
                "• 'How to check service status?'\n\n"
                "Which one?"
            )
        
        if any(word in lower for word in ["booking", "appointment", "calendar"]):
            return (
                f"I can help with bookings! Try asking:\n"
                "• 'How to create a booking?'\n"
                "• 'Does the system have a calendar?'\n"
                "• 'How to assign surveyors?'\n\n"
                "What do you need?"
            )
        
        if any(word in lower for word in ["document", "file"]):
            return (
                f"I can help with documents! Try asking:\n"
                "• 'How to upload a document?'\n"
                "• 'How are documents shared?'\n"
                "• 'How to view documents?'\n\n"
                "Which one interests you?"
            )
        
        # Generic fallback with personalization
        base = (
            "I'm not quite sure about that. Could you rephrase?\n\n"
            "I can help with:\n"
            "• **Clients** - Add, view, manage\n"
            "• **Services** - Title deeds and ground work\n"
            "• **Bookings** - Schedule field work\n"
            "• **Documents** - Upload and share\n"
            "• **Accounts** - Track finances\n\n"
            "What would you like to know?"
        )
        
        return f"{username}, {base}" if username else base
    
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
    Smart bot endpoint with full intelligence
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
            "clarification_enabled": config.ENABLE_CLARIFICATION,
            "onboarding_enabled": config.ENABLE_ONBOARDING,
            "similarity_threshold": config.SIMILARITY_MIN_SCORE,
            "medium_confidence_threshold": config.MEDIUM_CONFIDENCE,
            "high_confidence_threshold": config.HIGH_CONFIDENCE_SCORE
        }
    })


@csrf_exempt
def clear_session(request):
    """Clear user session history"""
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
    """Get user's conversation history"""
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    
    username = request.GET.get("username", "")
    
    if not username:
        return JsonResponse({"error": "Username required"}, status=400)
    
    session = SessionManager.sessions.get(username, {})
    history = session.get("conversation_history", [])
    
    return JsonResponse({
        "ok": True,
        "username": username,
        "history": history[-20:],
        "count": len(history),
        "last_topic": session.get("last_topic", "")
    })


@csrf_exempt
def clear_cache(request):
    """Clear all bot caches"""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    
    try:
        cache_keys = ['int:v3:*', 'search:v3:*', 'response:v3:*']
        
        cleared = 0
        for pattern in cache_keys:
            try:
                if hasattr(cache, 'delete_pattern'):
                    cleared += cache.delete_pattern(pattern)
                else:
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