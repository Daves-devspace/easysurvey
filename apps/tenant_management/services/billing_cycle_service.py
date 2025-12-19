import logging
import calendar
from datetime import date
from django.utils import timezone
from django.db.models import Q, Sum

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
        STEP 1: Run on the 1st of the Month. Generates Rent lines.
        """
        target_date = target_date or timezone.now().date()
        billing_month_start = target_date.replace(day=1) 
        
        logger.info(f"Starting Rent Roll for {billing_month_start.strftime('%b %Y')}...")
        
        leases = Lease.objects.filter(is_active=True).select_related('tenant', 'unit__property')
        if property_id:
            leases = leases.filter(unit__property_id=property_id)
            
        results = {"created": 0, "errors": 0, "skipped": 0, "details": []}
        
        for lease in leases:
            result = cls._process_single_lease_rent(lease, billing_month_start)
            results["details"].append(result)
            
            if result["status"] == "success":
                results["created"] += 1
            elif result["status"] == "skipped":
                results["skipped"] += 1
            elif result["status"] == "error":
                results["errors"] += 1
                logger.error(f"Error processing {result.get('tenant')}: {result.get('error')}")
                
        logger.info(f"Rent Roll Complete. Created: {results['created']}, Skipped: {results['skipped']}")
        return results

    @classmethod
    def _process_single_lease_rent(cls, lease, billing_month_date):
        try:
            # Calculate property specific start date (e.g. 5th vs 1st)
            billing_day = lease.tenant.property.billing_day
            last_day_of_month = calendar.monthrange(billing_month_date.year, billing_month_date.month)[1]
            safe_day = min(billing_day, last_day_of_month)
            actual_start_date = billing_month_date.replace(day=safe_day)

            # Validity Checks
            if actual_start_date < lease.start_date:
                return {"lease_id": lease.id, "tenant": lease.tenant.full_name, "status": "skipped", "reason": "Lease starts in future"}
            
            if lease.end_date and actual_start_date > lease.end_date:
                return {"lease_id": lease.id, "tenant": lease.tenant.full_name, "status": "skipped", "reason": "Lease ended"}

            # Duplicate Check
            existing_rent = InvoiceLine.objects.filter(
                lease=lease,
                line_type=InvoiceLine.LINE_RENT,
                invoice__billing_period_start__year=actual_start_date.year,
                invoice__billing_period_start__month=actual_start_date.month
            ).exists()

            if existing_rent:
                return {"lease_id": lease.id, "tenant": lease.tenant.full_name, "status": "skipped", "reason": "Rent already billed"}

            # Generate Invoice
            invoice = InvoiceService.upsert_rent_invoice_line_for_lease(lease, actual_start_date)
            
            # Apply Credits
            try:
                PaymentService.apply_credit_to_invoice(lease.tenant, invoice)
            except Exception as e:
                logger.warning(f"Failed to auto-apply credit: {e}")
            
            # Note on Arrears:
            # We do NOT add previous balance to this invoice object to avoid double-counting in the ledger.
            # The 'Total Due' sent to the user via SMS is calculated as (Current Invoice + Previous Unpaid Invoices).

            return {"lease_id": lease.id, "tenant": lease.tenant.full_name, "invoice_id": invoice.id, "status": "success"}
        except Exception as e:
            logger.error(f"Failed rent roll for Lease {lease.id}: {e}")
            return {"lease_id": lease.id, "tenant": lease.tenant.full_name, "status": "error", "error": str(e)}

    @classmethod
    def process_billing_day(cls, property_id=None, specific_date=None):
        """
        STEP 2: Run Daily. Finalizes invoices and sends SMS.
        Automates the workflow based on Property Policy.
        """
        target_date = specific_date or date.today()
        day_of_month = target_date.day
        
        # Find properties that bill on this day
        properties = Property.objects.filter(billing_day=day_of_month)
        if property_id:
            properties = properties.filter(id=property_id)
            
        for prop in properties:
            logger.info(f"Processing Billing Day for Property: {prop.name}")
            cls._finalize_property_invoices(prop, target_date)

        # Also sweep for any late-meter-readings that made pending invoices ready
        cls._finalize_pending_sweep()

    @classmethod
    def _finalize_property_invoices(cls, property_obj, target_date):
        """
        Finalizes ready invoices and triggers SMS.
        """
        # Look for invoices for this property in this period that are NOT yet finalized
        invoices = Invoice.objects.filter(
            tenant__property=property_obj,
            status__in=[Invoice.STATUS_DRAFT, Invoice.STATUS_PENDING],
            billing_period_end__year=target_date.year,
            billing_period_end__month=target_date.month
        )

        for invoice in invoices:
            if cls._check_invoice_readiness(invoice):
                # 1. Mark Finalized
                invoice.finalize() 
                logger.info(f"Finalized Invoice #{invoice.id} for {invoice.tenant.full_name}")
                
                # 2. Trigger SMS Task
                from apps.tenant_management.tasks import send_invoice_sms_task
                send_invoice_sms_task.delay(invoice.id)
                logger.info(f"Queued SMS notification for Invoice #{invoice.id}")

    @classmethod
    def _finalize_pending_sweep(cls):
        """
        Checks ALL invoices currently stuck in 'PENDING'. 
        If they are now ready (e.g. Meter Reading was added late), Finalize & Send SMS.
        """
        pending_invoices = Invoice.objects.filter(status=Invoice.STATUS_PENDING).select_related('tenant', 'tenant__property')
        
        for invoice in pending_invoices:
            if cls._check_invoice_readiness(invoice):
                invoice.finalize()
                logger.info(f"Finalized Pending Invoice #{invoice.id} (Late Catch-up) for {invoice.tenant.full_name}")
                
                from apps.tenant_management.tasks import send_invoice_sms_task
                send_invoice_sms_task.delay(invoice.id)

    @staticmethod
    def _check_invoice_readiness(invoice):
        """
        Business Logic: Is this invoice ready to send?
        
        Automation Rules:
        1. PREPAID: Ready immediately if Rent exists.
        2. METER: Ready only if Rent AND Water lines exist.
        """
        # 1. Must have Rent
        has_rent = invoice.lines.filter(line_type='RENT').exists()
        if not has_rent: return False
        
        # 2. Check Water Policy
        try:
            policy = invoice.tenant.property.water_policy
        except AttributeError: return False
        
        if policy == Property.METER:
            # For Metered, we MUST wait for the meter reading input (Water Line)
            has_water = invoice.lines.filter(line_type='WATER').exists()
            return has_water 
            
        elif policy == Property.PREPAID:
            # For Prepaid, we do NOT wait for water. Ready immediately.
            return True
        
        elif policy == Property.SHARED:
            # For Shared, assuming fixed cost logic handles it or it's manual
            return True
            
        return True