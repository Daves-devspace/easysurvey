from decimal import Decimal
from django.db import transaction
from apps.tenant_management.models import Tenant, Payment
from apps.tenant_management.services.payment_strategies import CreditApplicationStrategy, PaymentStrategy
from apps.tenant_management.services import BaseService
from apps.tenant_management.exceptions import InvalidTenantError
from django.db.models import Sum
import logging

logger = logging.getLogger(__name__)

class PaymentService(BaseService):
    """Service for handling payment processing."""
    
    @classmethod
    def process_payment(cls, tenant, amount, reference=None, method="Mpesa", invoice=None, apply_to_deposit=True):
        """
        Process a new incoming payment (Cash/Mpesa).
        """
        if not tenant or not hasattr(tenant, 'pk'):
            raise InvalidTenantError("Valid tenant is required")
            
        logger.info(f"Processing payment {amount} for {tenant.full_name}")
        
        strategy = PaymentStrategy(tenant, reference, method)
        result = strategy.execute(amount)
        
        # Update Cache
        from apps.tenant_management.models import TenantBalance
        TenantBalance.recalc_for_tenant(tenant)
        
        return result
    
    @classmethod
    def apply_credit_to_invoice(cls, tenant, invoice=None):
        """
        Apply available tenant credit (unallocated payments) to unpaid invoices.
        """
        logger.info(f"[PaymentService] Checking credit for {tenant.full_name} (ID: {tenant.id})...")
        
        # DEBUG: Log all unallocated payments to see what's happening
        all_unallocated = Payment.objects.filter(tenant=tenant, invoice__isnull=True)
        for p in all_unallocated:
            logger.debug(f"Found unallocated payment: ID={p.id}, Amount={p.amount}, Type={p.payment_type}, Ref={p.reference}")

        # Check credit availability
        available_credit = Payment.objects.filter(
            tenant=tenant,
            invoice__isnull=True
        ).exclude(payment_type='MIXED').aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        logger.info(f"[PaymentService] Found total available credit: {available_credit}")

        if available_credit <= 0:
            logger.info("[PaymentService] No credit available to apply.")
            return
        
        # Execute Strategy
        strategy = CreditApplicationStrategy(tenant)
        result = strategy.execute() 
        
        logger.info(f"[PaymentService] Credit application result: {result}")

        # Update Cache
        from apps.tenant_management.models import TenantBalance
        TenantBalance.recalc_for_tenant(tenant)
        
        return result