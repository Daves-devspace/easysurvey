"""
apps/tenant_management/helpers/logging_utils.py

Small logger factory for the tenant_management app.
Idempotent: calling get_logger() multiple times won't add duplicate handlers.

Features:
- Console (StreamHandler)
- Optional rotating file handler
- Simple, readable formatter
"""
from typing import Optional
import logging
from logging.handlers import RotatingFileHandler
import os


DEFAULT_LOG_FILENAME = os.environ.get("TENANT_MGMT_LOG", "tenant_management.log")
DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
DEFAULT_BACKUP_COUNT = 5


def _make_formatter() -> logging.Formatter:
    """
    Return a standard formatter. Keep it simple so it's easy to search logs.
    """
    fmt = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    return logging.Formatter(fmt=fmt, datefmt=datefmt)


def get_logger(
    name: str,
    level: int = logging.INFO,
    log_to_file: bool = True,
    filename: Optional[str] = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> logging.Logger:
    """
    Return a configured logger for `name`.

    - Avoids adding duplicate handlers if the logger already has handlers.
    - By default logs to stdout and a rotating file `tenant_management.log`.

    Args:
        name: logger name (e.g. "tenant_management.services")
        level: logging level (logging.DEBUG, INFO, etc.)
        log_to_file: whether to add a RotatingFileHandler
        filename: override default filename for file logging
        max_bytes: max file size before rotation
        backup_count: number of rotated files to keep

    Returns:
        logging.Logger
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # If handlers present, respect existing configuration (prevents duplicates)
    if logger.handlers:
        return logger

    formatter = _make_formatter()

    # Console handler
    console_h = logging.StreamHandler()
    console_h.setLevel(level)
    console_h.setFormatter(formatter)
    logger.addHandler(console_h)

    # Optional rotating file handler
    if log_to_file:
        filename = filename or DEFAULT_LOG_FILENAME
        try:
            file_h = RotatingFileHandler(
                filename, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
            )
            file_h.setLevel(level)
            file_h.setFormatter(formatter)
            logger.addHandler(file_h)
        except Exception:
            # Fail gracefully if file handler cannot be created (permission issues, etc.)
            logger.warning("Could not create file logging handler; continuing with console only.", exc_info=True)

    # Avoid propagation to the root logger (so messages aren't duplicated)
    logger.propagate = False

    return logger
