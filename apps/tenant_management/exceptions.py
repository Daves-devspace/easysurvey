class BillingException(Exception):
    """Base exception for billing errors"""
    pass

class PaymentProcessingError(BillingException):
    """Payment processing failed"""
    pass

class InvoiceGenerationError(BillingException):
    """Invoice generation failed"""
    pass

class InvalidTenantError(BillingException):
    """Invalid tenant provided"""
    pass

class DatabaseLockError(BillingException):
    """Database lock acquisition failed"""
    pass