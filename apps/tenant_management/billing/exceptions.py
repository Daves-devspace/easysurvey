# =================================================================
# apps/tenant_management/billing/exceptions.py
# =================================================================

class BillingError(Exception):
    """Base exception for billing operations."""
    pass

class InvoiceGenerationError(BillingError):
    """Raised when invoice generation fails."""
    pass

class PaymentProcessingError(BillingError):
    """Raised when payment processing fails."""
    pass

class InvalidTenantError(BillingError):
    """Raised when tenant is invalid or not found."""
    pass

class DuplicateInvoiceError(BillingError):
    """Raised when attempting to create duplicate invoice."""
    pass

class InsufficientPaymentError(BillingError):
    """Raised when payment amount is insufficient."""
    pass

class DepositError(BillingError):
    """Raised when deposit operations fail."""
    pass