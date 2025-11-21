"""
apps/tenant_management/helpers/monitoring_utils.py

Provides a decorator factory `monitor_performance(metric_name)`.

Behavior:
- Logs function start and finish (with elapsed time) at DEBUG.
- Logs exceptions with stack trace and elapsed time.
- If `prometheus_client` is installed, records timing into a Histogram named
  `app_{metric_name}_seconds`. If not installed, behaves as a pure-logging decorator.

Usage:
    from apps.tenant_management.helpers.monitoring_utils import monitor_performance

    @monitor_performance("execute_with_retry")
    def execute_with_retry(...):
        ...
"""
from typing import Callable, Any
import time
import functools
import logging

# Optional Prometheus instrumentation
_PROM_AVAILABLE = False
_HISTOGRAMS = {}

try:
    # local import to avoid hard dependency; if prometheus_client exists we use it
    from prometheus_client import Histogram  # type: ignore
    _PROM_AVAILABLE = True
except Exception:
    _PROM_AVAILABLE = False

# Use a consistent logger name for monitoring output so it's easy to find.
_DEFAULT_LOGGER_NAME = "tenant_management.monitoring"


def _get_histogram(metric_name: str):
    """
    Lazily create and cache a Histogram for the given metric_name if Prometheus is available.
    """
    if not _PROM_AVAILABLE:
        return None

    if metric_name in _HISTOGRAMS:
        return _HISTOGRAMS[metric_name]

    # Prometheus metric names must match regex [a-zA-Z_:][a-zA-Z0-9_:]*
    prom_name = f"app_{metric_name}_seconds"
    try:
        hist = Histogram(prom_name, f"Execution time (seconds) for {metric_name}")
        _HISTOGRAMS[metric_name] = hist
        return hist
    except Exception:
        # If histogram creation fails (name collision etc.), ignore and proceed without metrics.
        logging.getLogger(_DEFAULT_LOGGER_NAME).warning(
            "Unable to create Prometheus histogram for %s; continuing without prometheus.", metric_name, exc_info=True
        )
        return None


def monitor_performance(metric_name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Decorator factory to monitor a function's performance under `metric_name`.

    Args:
        metric_name: human-friendly metric id (used for Prometheus metric name and logs)

    Returns:
        decorator that wraps a callable, logs start/stop and records Prometheus timing if available.
    """
    histogram = _get_histogram(metric_name)

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            logger = logging.getLogger(_DEFAULT_LOGGER_NAME)
            qualname = getattr(func, "__qualname__", func.__name__)
            logger.debug("Starting %s [%s]", qualname, metric_name)
            start = time.monotonic()

            # If we have a Prometheus histogram, use its context manager.
            try:
                if histogram is not None:
                    with histogram.time():
                        return func(*args, **kwargs)
                else:
                    return func(*args, **kwargs)
            except Exception as exc:
                elapsed = time.monotonic() - start
                # Log exception with elapsed time
                logger.exception("Exception in %s [%s] after %.6fs: %s", qualname, metric_name, elapsed, exc)
                raise
            finally:
                elapsed = time.monotonic() - start
                logger.debug("Finished %s [%s] in %.6fs", qualname, metric_name, elapsed)

        return wrapper

    return decorator
