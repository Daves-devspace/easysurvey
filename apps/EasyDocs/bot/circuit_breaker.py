# apps/EasyDocs/bot/circuit_breaker.py
import time, threading
from typing import Optional

_lock = threading.Lock()
_fail_count = 0
_disabled_until = 0.0

FAIL_THRESHOLD = 3       # consecutive fails to open
COOLDOWN_SECONDS = 60    # how long to stay open

def record_success():
    global _fail_count, _disabled_until
    with _lock:
        _fail_count = 0
        _disabled_until = 0.0

def record_failure():
    global _fail_count, _disabled_until
    with _lock:
        _fail_count += 1
        if _fail_count >= FAIL_THRESHOLD:
            _disabled_until = time.time() + COOLDOWN_SECONDS

def is_open() -> bool:
    with _lock:
        return time.time() < _disabled_until

def get_disabled_until() -> Optional[float]:
    with _lock:
        return _disabled_until if _disabled_until > 0 else None
