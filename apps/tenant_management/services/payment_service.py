from decimal import Decimal
from django.db import transaction
from apps.tenant_management.models import Tenant
from apps.tenant_management.services.payment_strategies import  CreditApplicationStrategy,PaymentStrategy
#from apps.tenant_management.services import BaseService
from apps.tenant_management.services import BaseService
from apps.tenant_management.exceptions import InvalidTenantError
#from apps.tenant_management.helpers.performance_monitoring import monitor_performance
from django.db.models import Sum
from apps.tenant_management.helpers.logging_utils import get_logger

logger = get_logger("tenant_management.payment_service")

class PaymentService(BaseService):
    """Service for handling payment processing."""
    
    @classmethod
 #   @monitor_performance("process_payment")
    def process_payment(cls, tenant, amount, reference=None, method="Mpesa", invoice=None):
        """
        Process a payment for a tenant.
        """
        # Validate tenant
        if not tenant or not hasattr(tenant, 'pk'):
            raise InvalidTenantError("Valid tenant is required")
            
        try:
            # Ensure tenant exists in database
            Tenant.objects.get(pk=tenant.pk)
        except Tenant.DoesNotExist:
            raise InvalidTenantError(f"Tenant with ID {tenant.pk} does not exist")
        
        logger.info(f"Starting payment processing for tenant {tenant.full_name}", {
            "tenant_id": tenant.pk,
            "amount": amount,
            "method": method,
            "reference": reference
        })
        
        # Use strategy pattern based on whether this is a new payment or credit application
        if amount is not None:
            strategy = PaymentStrategy(tenant, reference, method)
        else:
            strategy = CreditApplicationStrategy(tenant, reference, method)
        
        # Execute the strategy
        result = strategy.execute(amount)
        
        # Recalculate tenant balance
        from apps.tenant_management.models import TenantBalance
        TenantBalance.recalc_for_tenant(tenant)
        
        logger.info(f"Payment processing completed for tenant {tenant.full_name}", {
            "tenant_id": tenant.pk,
            "result": result
        })
        
        return result
    
    @classmethod
 #   @monitor_performance("apply_credit_to_invoice")
    def apply_credit_to_invoice(cls, tenant, invoice):
        """
        Apply available tenant credit to a specific invoice.
        """
        logger.info(f"Applying credit to invoice {invoice.pk} for tenant {tenant.full_name}", {
            "tenant_id": tenant.pk,
            "invoice_id": invoice.pk
        })
        
        # Check for available credit
        from apps.tenant_management.models import Payment
        available_credit = Payment.objects.filter(
            tenant=tenant,
            invoice__isnull=True
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        if available_credit <= 0:
            logger.info(f"No available credit for tenant {tenant.full_name}")
            return {
                "applied_to_deposit": "0.00",
                "applied_to_invoices": "0.00", 
                "stored_as_credit": "0.00",
                "unallocated": "0.00"
            }
        
        # Use credit application strategy
        strategy = CreditApplicationStrategy(tenant)
        result = strategy.execute()
        
        # Recalculate tenant balance
        from apps.tenant_management.models import TenantBalance
        TenantBalance.recalc_for_tenant(tenant)
        
        logger.info(f"Credit application completed for tenant {tenant.full_name}", {
            "tenant_id": tenant.pk,
            "result": result
        })
        
        return result