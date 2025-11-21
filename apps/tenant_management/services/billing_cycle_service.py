import logging
from datetime import date
from django.utils import timezone
from django.db.models import Q

from apps.tenant_management.models import Property, Lease, Invoice
from apps.tenant_management.services.invoice_service import InvoiceService
from apps.tenant_management.services.payment_service import PaymentService
from apps.tenant_management.services import BaseService

logger = logging.getLogger(__name__)

class BillingCycleService(BaseService):
    """
    Orchestrator for Mass Billing Operations.
    Replaces 'apps/tenant_management/billing/utils.py'.
    Should be called by Celery Tasks or Cron Jobs.
    """

    @classmethod
    def generate_rent_roll(cls, target_date: date = None, property_id=None):
        """
        STEP 1: Run on the 1st of the Month.
        Action: Generates invoices with Rent line items.
        Feature: Automatically applies existing tenant credits to these new invoices.
        """
        target_date = target_date or timezone.now().date()
        # Always force the 1st of the month to ensure we catch the correct period
        billing_date = target_date.replace(day=1) 
        
        logger.info(f"Starting Rent Roll Generation for {billing_date.strftime('%b %Y')}...")
        
        # Fetch active leases with related data to minimize DB hits
        leases = Lease.objects.filter(is_active=True).select_related('tenant', 'unit__property')
        if property_id:
            leases = leases.filter(unit__property_id=property_id)
            
        results = {
            "created": 0,
            "errors": 0,
            "details": []
        }
        
        for lease in leases:
            result = cls._process_single_lease_rent(lease, billing_date)
            results["details"].append(result)
            
            if result["status"] == "success":
                results["created"] += 1
            elif result["status"] == "error":
                results["errors"] += 1
                
        logger.info(f"Rent Roll Complete. Created: {results['created']}, Errors: {results['errors']}")
        return results

    @classmethod
    def _process_single_lease_rent(cls, lease, billing_date):
        """
        Helper to process a single lease safely.
        1. Create Invoice with Rent.
        2. Apply Credits (if any).
        """
        try:
            # 1. Create Invoice & Add Rent (Rent Forward)
            invoice = InvoiceService.upsert_rent_invoice_line_for_lease(lease, billing_date)
            
            # 2. Auto-Apply Credits
            # If the tenant has unallocated payments, apply them now.
            try:
                PaymentService.apply_credit_to_invoice(lease.tenant, invoice)
            except Exception as e:
                logger.warning(f"Failed to auto-apply credit for {lease.tenant}: {e}")

            return {
                "lease_id": lease.id,
                "tenant": lease.tenant.full_name,
                "invoice_id": invoice.id,
                "status": "success"
            }
        except Exception as e:
            logger.error(f"Failed to generate rent for Lease {lease.id}: {e}", exc_info=True)
            return {
                "lease_id": lease.id,
                "tenant": lease.tenant.full_name,
                "status": "error",
                "error": str(e)
            }

    @classmethod
    def process_billing_day(cls, property_id=None, specific_date=None):
        """
        STEP 2: Run Daily.
        Action: Finalizes invoices if today matches the property's 'billing_day'.
        """
        target_date = specific_date or date.today()
        day_of_month = target_date.day
        
        # 1. Find properties that bill on this day
        properties = Property.objects.filter(billing_day=day_of_month)
        if property_id:
            properties = properties.filter(id=property_id)
            
        for prop in properties:
            logger.info(f"Processing Billing Day for Property: {prop.name}")
            cls._finalize_property_invoices(prop, target_date)

    @classmethod
    def _finalize_property_invoices(cls, property_obj, target_date):
        """
        Helper: Looks for invoices ready to be finalized (Rent + Water).
        Only looks at invoices ending in the current billing month.
        """
        invoices = Invoice.objects.filter(
            tenant__property=property_obj,
            status__in=[Invoice.STATUS_DRAFT, Invoice.STATUS_PENDING],
            billing_period_end__year=target_date.year,
            billing_period_end__month=target_date.month
        )

        for invoice in invoices:
            if cls._check_invoice_readiness(invoice):
                invoice.finalize() # Sets status to FINALIZED
                
                # Future Integration:
                # NotificationService.send_invoice_notification(invoice)
                
                logger.info(f"Finalized Invoice #{invoice.id} for {invoice.tenant.full_name}")

    @staticmethod
    def _check_invoice_readiness(invoice):
        """
        Business Logic: Is this invoice ready to send?
        """
        # 1. Must have Rent
        has_rent = invoice.lines.filter(line_type='RENT').exists()
        if not has_rent: 
            return False
        
        # 2. Check Water Policy
        policy = invoice.tenant.property.water_policy
        if policy == Property.METER:
            # If metered, we EXPECT a water line before finalizing
            has_water = invoice.lines.filter(line_type='WATER').exists()
            return has_water 
        
        # Shared/Prepaid don't need water lines to finalize
        return True