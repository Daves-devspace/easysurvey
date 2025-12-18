import logging
import calendar
from datetime import date
from django.utils import timezone
from django.db.models import Q

from apps.tenant_management.models import Property, Lease, Invoice, InvoiceLine
from apps.tenant_management.services.invoice_service import InvoiceService
from apps.tenant_management.services.payment_service import PaymentService
from apps.tenant_management.services import BaseService

logger = logging.getLogger(__name__)

class BillingCycleService(BaseService):
    """
    Orchestrator for Mass Billing Operations.
    """

    @classmethod
    def generate_rent_roll(cls, target_date: date = None, property_id=None):
        """
        STEP 1: Run on the 1st of the Month.
        Generates invoices with Rent line items.
        """
        target_date = target_date or timezone.now().date()
        # We start with the 1st, but individual leases will adjust based on billing_day
        billing_month_start = target_date.replace(day=1) 
        
        logger.info(f"Starting Rent Roll Generation for {billing_month_start.strftime('%b %Y')} (Target: {target_date})...")
        
        leases = Lease.objects.filter(is_active=True).select_related('tenant', 'unit__property')
        if property_id:
            leases = leases.filter(unit__property_id=property_id)
            
        results = {"created": 0, "errors": 0, "skipped": 0, "details": []}
        
        for lease in leases:
            # We pass billing_month_start (e.g., Dec 1)
            result = cls._process_single_lease_rent(lease, billing_month_start)
            results["details"].append(result)
            
            if result["status"] == "success":
                results["created"] += 1
                logger.debug(f"Processed rent for {result.get('tenant')} (Lease {result.get('lease_id')})")
            elif result["status"] == "skipped":
                results["skipped"] += 1
                logger.debug(f"Skipped rent for {result.get('tenant')}: {result.get('reason')}")
            elif result["status"] == "error":
                results["errors"] += 1
                logger.error(f"Error processing {result.get('tenant')}: {result.get('error')}")
                
        logger.info(f"Rent Roll Complete. Processed/Created: {results['created']}, Skipped: {results['skipped']}, Errors: {results['errors']}")
        return results

    @classmethod
    def _process_single_lease_rent(cls, lease, billing_month_date):
        """
        Process rent for a lease safely.
        Calculates the correct billing start date based on Property.billing_day.
        """
        try:
            # --- FIX: Determine exact start date for this property ---
            # If billing_day is 5, and we want Dec rent, we aim for Dec 5.
            # If we used Dec 1, it might fall into Nov period for some logic.
            billing_day = lease.tenant.property.billing_day
            
            # Handle short months (e.g. billing day 31 in Feb)
            # We find the max valid day for the target month
            last_day_of_month = calendar.monthrange(billing_month_date.year, billing_month_date.month)[1]
            safe_day = min(billing_day, last_day_of_month)
            
            # The actual date we want the invoice to start on
            actual_start_date = billing_month_date.replace(day=safe_day)

            # 0. PERIOD VALIDITY CHECK: Don't bill before start or after end
            if actual_start_date < lease.start_date:
                return {
                    "lease_id": lease.id, 
                    "tenant": lease.tenant.full_name, 
                    "status": "skipped", 
                    "reason": f"Lease starts in future ({lease.start_date})"
                }
            
            if lease.end_date and actual_start_date > lease.end_date:
                return {
                    "lease_id": lease.id, 
                    "tenant": lease.tenant.full_name, 
                    "status": "skipped", 
                    "reason": f"Lease ended ({lease.end_date})"
                }

            # 1. DUPLICATE CHECK
            # Check for Rent line matching the ACTUAL start date month/year
            existing_rent = InvoiceLine.objects.filter(
                lease=lease,
                line_type=InvoiceLine.LINE_RENT,
                invoice__billing_period_start__year=actual_start_date.year,
                invoice__billing_period_start__month=actual_start_date.month
            ).exists()

            if existing_rent:
                return {
                    "lease_id": lease.id, 
                    "tenant": lease.tenant.full_name, 
                    "status": "skipped", 
                    "reason": "Rent already billed for this month"
                }

            # 2. Generate Invoice using the CORRECTED date
            invoice = InvoiceService.upsert_rent_invoice_line_for_lease(lease, actual_start_date)
            
            # 3. Auto-Apply Credits
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
        Finalizes invoices if today matches the property's 'billing_day'.
        """
        target_date = specific_date or date.today()
        day_of_month = target_date.day
        
        properties = Property.objects.filter(billing_day=day_of_month)
        if property_id:
            properties = properties.filter(id=property_id)
            
        for prop in properties:
            logger.info(f"Processing Billing Day for Property: {prop.name}")
            cls._finalize_property_invoices(prop, target_date)

    @classmethod
    def _finalize_property_invoices(cls, property_obj, target_date):
        invoices = Invoice.objects.filter(
            tenant__property=property_obj,
            status__in=[Invoice.STATUS_DRAFT, Invoice.STATUS_PENDING],
            billing_period_end__year=target_date.year,
            billing_period_end__month=target_date.month
        )

        for invoice in invoices:
            if cls._check_invoice_readiness(invoice):
                invoice.finalize() 
                logger.info(f"Finalized Invoice #{invoice.id} for {invoice.tenant.full_name}")

    @staticmethod
    def _check_invoice_readiness(invoice):
        # 1. Must have Rent
        has_rent = invoice.lines.filter(line_type='RENT').exists()
        if not has_rent: return False
        
        # 2. Check Water Policy
        try:
            policy = invoice.tenant.property.water_policy
        except AttributeError: return False
        
        if policy == Property.METER:
            has_water = invoice.lines.filter(line_type='WATER').exists()
            return has_water 
        elif policy == Property.PREPAID:
            return True
            
        return True