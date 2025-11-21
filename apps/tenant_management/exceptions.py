"""
Custom exceptions for the tenant management application.
Provides specific exception types for different error scenarios.
"""


class TenantManagementException(Exception):
    """Base exception for all tenant management errors."""
    
    def __init__(self, message: str, code: str = None, details: dict = None):
        self.message = message
        self.code = code or self.__class__.__name__
        self.details = details or {}
        super().__init__(self.message)
    
    def to_dict(self):
        """Convert exception to dictionary for API responses."""
        return {
            'error': self.code,
            'message': self.message,
            'details': self.details
        }


# Database-related exceptions
class DatabaseLockError(TenantManagementException):
    """Raised when database lock cannot be acquired after retries."""
    pass


class DatabaseIntegrityError(TenantManagementException):
    """Raised when database integrity constraints are violated."""
    pass


# Service-level exceptions
class ServiceValidationError(TenantManagementException):
    """Raised when service-level validation fails."""
    pass


class ServiceOperationError(TenantManagementException):
    """Raised when a service operation fails."""
    pass


# Business logic exceptions
class TenantAlreadyExistsError(TenantManagementException):
    """Raised when attempting to create a tenant that already exists."""
    pass


class UnitNotAvailableError(TenantManagementException):
    """Raised when attempting to assign a tenant to an unavailable unit."""
    pass


class LeaseValidationError(TenantManagementException):
    """Raised when lease data validation fails."""
    pass


class PaymentScheduleError(TenantManagementException):
    """Raised when payment schedule generation fails."""
    pass


class InsufficientPaymentError(TenantManagementException):
    """Raised when payment amount is insufficient."""
    pass


# Authorization exceptions
class UnauthorizedOperationError(TenantManagementException):
    """Raised when user is not authorized to perform an operation."""
    pass


class ResourceNotFoundError(TenantManagementException):
    """Raised when a requested resource is not found."""
    pass


# Configuration exceptions
class ConfigurationError(TenantManagementException):
    """Raised when there's a configuration error."""
    pass

class InvalidTenantError(TenantManagementException):
    """Raised when the provided tenant is invalid."""
    pass


class BillingError(Exception):
    """Base exception for billing operations."""
    pass

class InvoiceGenerationError(BillingError):
    pass

class PaymentProcessingError(BillingError):
    pass

class InvalidTenantError(BillingError):
    pass

class DuplicateInvoiceError(BillingError):
    pass

class InsufficientPaymentError(BillingError):
    pass