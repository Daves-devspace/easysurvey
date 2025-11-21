"""
Base service class for tenant management operations.
Provides transaction management, retry logic, and common utilities.
"""
from django.db import transaction
from django.db import OperationalError, IntegrityError
from django.core.exceptions import ValidationError
from typing import Callable, Any, Optional, Dict
import time

from apps.tenant_management.exceptions import (
    DatabaseLockError,
    ServiceValidationError,
    ServiceOperationError
)
from apps.tenant_management.helpers.logging_utils import get_logger
from apps.tenant_management.helpers.monitoring_utils import monitor_performance

logger = get_logger("tenant_management.services")


class BaseService:
    """
    Base service class with common functionality for all business logic services.
    
    Features:
    - Transaction management with automatic rollback
    - Retry logic for database lock contention
    - Performance monitoring integration
    - Centralized error handling
    - Validation support
    
    Usage:
        class TenantService(BaseService):
            @classmethod
            def create_tenant(cls, data):
                return cls.execute_with_retry(cls._do_create_tenant, data)
    """
    
    MAX_RETRIES = 3
    INITIAL_BACKOFF = 0.1  # seconds
    MAX_BACKOFF = 2.0  # seconds
    
    @classmethod
    @monitor_performance("execute_with_retry")
    def execute_with_retry(
        cls, 
        operation: Callable, 
        *args, 
        **kwargs
    ) -> Any:
        """
        Execute a database operation with retry logic for lock contention.
        
        Args:
            operation: The callable to execute
            *args: Positional arguments for the operation
            **kwargs: Keyword arguments for the operation
            
        Returns:
            Result of the operation
            
        Raises:
            DatabaseLockError: If max retries exceeded
            ServiceOperationError: For other operation failures
        """
        retries = 0
        last_error = None
        
        while retries < cls.MAX_RETRIES:
            try:
                with transaction.atomic():
                    result = operation(*args, **kwargs)
                    logger.debug(
                        f"Operation {operation.__name__} completed successfully"
                    )
                    return result
                    
            except OperationalError as e:
                if cls._is_lock_error(e):
                    retries += 1
                    last_error = e
                    
                    if retries < cls.MAX_RETRIES:
                        wait_time = min(
                            cls.INITIAL_BACKOFF * (2 ** retries),
                            cls.MAX_BACKOFF
                        )
                        logger.warning(
                            f"Database lock contention in {operation.__name__}, "
                            f"retry {retries}/{cls.MAX_RETRIES} after {wait_time}s: {e}"
                        )
                        time.sleep(wait_time)
                    else:
                        logger.error(
                            f"Max retries exceeded for {operation.__name__}: {e}"
                        )
                else:
                    # Non-lock operational error
                    logger.error(
                        f"Operational error in {operation.__name__}: {e}",
                        exc_info=True
                    )
                    raise ServiceOperationError(
                        f"Database operation failed: {str(e)}"
                    ) from e
                    
            except IntegrityError as e:
                # Data integrity violation (unique constraints, foreign keys, etc.)
                logger.error(
                    f"Integrity error in {operation.__name__}: {e}",
                    exc_info=True
                )
                raise ServiceValidationError(
                    f"Data integrity violation: {str(e)}"
                ) from e
                
            except ValidationError as e:
                # Django validation error
                logger.warning(f"Validation error in {operation.__name__}: {e}")
                raise ServiceValidationError(str(e)) from e
                
            except Exception as e:
                # Unexpected error
                logger.error(
                    f"Unexpected error in {operation.__name__}: {e}",
                    exc_info=True
                )
                raise ServiceOperationError(
                    f"Operation failed: {str(e)}"
                ) from e
        
        # Max retries exceeded
        raise DatabaseLockError(
            f"Failed to acquire database lock after {cls.MAX_RETRIES} retries. "
            f"Last error: {last_error}"
        )
    
    @classmethod
    def _is_lock_error(cls, error: OperationalError) -> bool:
        """
        Check if an OperationalError is a lock/deadlock error.
        
        Args:
            error: The OperationalError to check
            
        Returns:
            True if it's a lock-related error
        """
        error_msg = str(error).lower()
        lock_indicators = [
            'deadlock',
            'lock',
            'locked',
            'database is locked',
            'lock wait timeout',
            'lock timeout',
        ]
        return any(indicator in error_msg for indicator in lock_indicators)
    
    @classmethod
    @monitor_performance("validate_and_execute")
    def validate_and_execute(
        cls,
        validator: Callable,
        operation: Callable,
        *args,
        **kwargs
    ) -> Any:
        """
        Validate data before executing operation with retry logic.
        
        Args:
            validator: Validation function (should raise ValidationError if invalid)
            operation: The operation to execute after validation
            *args: Arguments for both validator and operation
            **kwargs: Keyword arguments for both validator and operation
            
        Returns:
            Result of the operation
            
        Raises:
            ServiceValidationError: If validation fails
            DatabaseLockError: If max retries exceeded
            ServiceOperationError: For other failures
        """
        try:
            # Run validation first (outside transaction)
            validator(*args, **kwargs)
            logger.debug(f"Validation passed for {operation.__name__}")
        except ValidationError as e:
            logger.warning(f"Validation failed for {operation.__name__}: {e}")
            raise ServiceValidationError(str(e)) from e
        except Exception as e:
            logger.error(f"Validator error for {operation.__name__}: {e}")
            raise ServiceValidationError(f"Validation error: {str(e)}") from e
        
        # Execute operation with retry logic
        return cls.execute_with_retry(operation, *args, **kwargs)
    
    @classmethod
    def get_or_create_safe(
        cls,
        model_class,
        defaults: Optional[Dict] = None,
        **lookup_fields
    ) -> tuple:
        """
        Thread-safe get_or_create with retry logic.
        
        Args:
            model_class: Django model class
            defaults: Default values for creation
            **lookup_fields: Fields to look up the object
            
        Returns:
            Tuple of (instance, created)
            
        Example:
            tenant, created = BaseService.get_or_create_safe(
                Tenant,
                defaults={'status': 'active'},
                email='john@example.com'
            )
        """
        defaults = defaults or {}
        
        def _get_or_create():
            return model_class.objects.get_or_create(
                defaults=defaults,
                **lookup_fields
            )
        
        return cls.execute_with_retry(_get_or_create)
    
    @classmethod
    def bulk_create_safe(
        cls,
        model_class,
        objects: list,
        batch_size: int = 100,
        ignore_conflicts: bool = False
    ) -> list:
        """
        Safely bulk create objects with retry logic.
        
        Args:
            model_class: Django model class
            objects: List of model instances to create
            batch_size: Number of objects to create per batch
            ignore_conflicts: Whether to ignore conflicts (for upserts)
            
        Returns:
            List of created objects
        """
        def _bulk_create():
            return model_class.objects.bulk_create(
                objects,
                batch_size=batch_size,
                ignore_conflicts=ignore_conflicts
            )
        
        return cls.execute_with_retry(_bulk_create)
    
    @classmethod
    @monitor_performance("execute_in_transaction")
    def execute_in_transaction(cls, *operations) -> list:
        """
        Execute multiple operations in a single transaction with retry logic.
        
        Args:
            *operations: Tuples of (callable, args, kwargs)
            
        Returns:
            List of results from each operation
            
        Example:
            results = BaseService.execute_in_transaction(
                (create_tenant, (data,), {}),
                (create_lease, (lease_data,), {}),
                (send_notification, (), {'tenant_id': tenant.id})
            )
        """
        def _execute_all():
            results = []
            for operation, args, kwargs in operations:
                result = operation(*args, **kwargs)
                results.append(result)
            return results
        
        return cls.execute_with_retry(_execute_all)
    
    @classmethod
    def log_operation(
        cls,
        operation_name: str,
        entity_type: str,
        entity_id: Any,
        user_id: Optional[Any] = None,
        metadata: Optional[Dict] = None
    ) -> None:
        """
        Log a service operation for audit trail.
        
        Args:
            operation_name: Name of the operation (e.g., 'create_tenant')
            entity_type: Type of entity (e.g., 'tenant', 'lease')
            entity_id: ID of the entity
            user_id: ID of the user performing the operation
            metadata: Additional metadata to log
        """
        log_data = {
            'operation': operation_name,
            'entity_type': entity_type,
            'entity_id': entity_id,
            'user_id': user_id,
            'metadata': metadata or {}
        }
        logger.info(f"Service operation: {operation_name}", extra=log_data)