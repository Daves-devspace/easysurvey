from django.db import transaction
from django.db import OperationalError
from apps.tenant_management.exceptions import DatabaseLockError
from apps.tenant_management.utils.logging_utils import get_logger
from apps.tenant_management.utils.monitoring_utils import monitor_performance

logger = get_logger("tenant_management.services")

class BaseService:
    """Base service class with common functionality."""
    
    MAX_RETRIES = 3
    
    @classmethod
    @monitor_performance("execute_with_retry")
    def execute_with_retry(cls, operation, *args, **kwargs):
        """
        Execute a database operation with retry logic for lock contention.
        """
        retries = 0
        while retries < cls.MAX_RETRIES:
            try:
                with transaction.atomic():
                    return operation(*args, **kwargs)
            except OperationalError as e:
                if 'deadlock' in str(e).lower() or 'lock' in str(e).lower():
                    retries += 1
                    wait_time = 0.1 * (2 ** retries)  # Exponential backoff
                    logger.warning(
                        f"Database lock contention detected, retry {retries}/{cls.MAX_RETRIES} "
                        f"after {wait_time}s: {e}"
                    )
                    import time
                    time.sleep(wait_time)
                else:
                    raise
        raise DatabaseLockError(f"Failed to acquire database lock after {cls.MAX_RETRIES} retries")