from django.db import transaction
from decimal import Decimal
from datetime import date
from apps.tenant_management.models import Invoice, Lease
from apps.tenant_management.helpers.date_helpers import get_billing_period_for_date
from apps.tenant_management.services import BaseService
import logging

logger = logging.getLogger(__name__)

class BillingService(BaseService):
    """Service for handling billing period calculations and invoice retrieval."""
    
    @classmethod
    def get_or_create_monthly_invoice(cls, tenant, billing_date: date):
        """
        Get or create invoice for the tenant respecting the property's billing_day.
        Uses retry logic to handle database lock contention.
        """
        from apps.tenant_management.helpers.date_helpers import normalize_billing_day_for_month
        
        billing_day = tenant.property.billing_day
        start, end = get_billing_period_for_date(billing_date, billing_day)
        
        def operation():
            # Try to get existing invoice first
            invoice = Invoice.objects.filter(
                tenant=tenant, 
                billing_period_start=start, 
                billing_period_end=end
            ).first()
            
            if invoice:
                return invoice
                
            # Create new invoice if it doesn't exist
            return Invoice.objects.create(
                tenant=tenant,
                billing_period_start=start,
                billing_period_end=end,
                status=Invoice.STATUS_DRAFT,
            )
        
        return cls.execute_with_retry(operation)
    
    @classmethod
    def get_or_create_invoice_for_period(cls, tenant, start_date: date, end_date: date):
        """
        Get or create invoice for a specific billing period.
        Uses retry logic to handle database lock contention.
        """
        def operation():
            # Try to get existing invoice first
            invoice = Invoice.objects.filter(
                tenant=tenant, 
                billing_period_start=start_date, 
                billing_period_end=end_date
            ).first()
            
            if invoice:
                return invoice
                
            # Create new invoice if it doesn't exist
            return Invoice.objects.create(
                tenant=tenant,
                billing_period_start=start_date,
                billing_period_end=end_date,
                status=Invoice.STATUS_DRAFT,
            )
        
        return cls.execute_with_retry(operation)