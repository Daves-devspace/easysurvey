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
    def get_or_create_monthly_invoice(cls, tenant, billing_date: date, lease=None):
        """
        Get or create invoice for the tenant/lease.
        Supports lease separation via the 'lease' FK.
        """
        billing_day = tenant.property.billing_day
        start, end = get_billing_period_for_date(billing_date, billing_day)
        
        def operation():
            query_args = {
                'tenant': tenant,
                'billing_period_start': start,
                'billing_period_end': end
            }
            # Crucial: Filter by lease if provided
            if lease:
                query_args['lease'] = lease
            
            invoice = Invoice.objects.filter(**query_args).first()
            
            if invoice:
                return invoice
                
            return Invoice.objects.create(
                tenant=tenant,
                lease=lease, # Link to specific lease
                billing_period_start=start,
                billing_period_end=end,
                status=Invoice.STATUS_DRAFT,
            )
        
        return cls.execute_with_retry(operation)
    
    @classmethod
    def get_or_create_invoice_for_period(cls, tenant, start_date: date, end_date: date, lease=None):
        def operation():
            query_args = {
                'tenant': tenant,
                'billing_period_start': start_date,
                'billing_period_end': end_date
            }
            if lease:
                query_args['lease'] = lease

            invoice = Invoice.objects.filter(**query_args).first()
            
            if invoice:
                return invoice
                
            return Invoice.objects.create(
                tenant=tenant,
                lease=lease,
                billing_period_start=start_date,
                billing_period_end=end_date,
                status=Invoice.STATUS_DRAFT,
            )
        return cls.execute_with_retry(operation)