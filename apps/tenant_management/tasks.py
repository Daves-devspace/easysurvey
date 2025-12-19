import logging
from celery import shared_task
from django.utils import timezone
from datetime import date

# Import Service Orchestrators
from apps.tenant_management.services.billing_cycle_service import BillingCycleService
from apps.tenant_management.services.invoice_service import InvoiceService
from apps.tenant_management.models import MeterReading, Invoice
from apps.tenant_management.comm.sms_utils import send_invoice_notification
from apps.tenant_management.comm.mobile_sasa import MobileSasaAPI

logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# TASK 1: MONTHLY RENT ROLL
# ---------------------------------------------------------
@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def generate_monthly_invoices(self, run_date: str | None = None):
    ref_date = date.fromisoformat(run_date) if run_date else timezone.now().date()
    logger.info("🚀 Starting Monthly Rent Roll for date=%s", ref_date)

    try:
        result = BillingCycleService.generate_rent_roll(target_date=ref_date)
        logger.info("✅ Rent Roll Complete. Created=%s, Errors=%s", result["created"], result["errors"])
        return result
    except Exception:
        logger.exception("❌ Failed during Rent Roll generation")
        raise

# ---------------------------------------------------------
# TASK 2: DAILY FINALIZATION
# ---------------------------------------------------------
@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def finalize_daily_invoices(self):
    today = timezone.now().date()
    logger.info("🔄 Running Daily Invoice Finalization Check for %s", today)

    try:
        BillingCycleService.process_billing_day(specific_date=today)
        logger.info("✅ Daily Finalization Check Complete.")
        return True
    except Exception:
        logger.exception("❌ Failed during Daily Finalization Check")
        raise

# ---------------------------------------------------------
# TASK 3: METER READING PROCESSING
# ---------------------------------------------------------
@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def process_new_meter_reading(self, reading_id: int):
    try:
        mr = MeterReading.objects.select_related('unit', 'unit__property').get(pk=reading_id)
    except MeterReading.DoesNotExist:
        logger.warning("⚠️ MeterReading %s not found. Skipping.", reading_id)
        return None

    if mr.current_reading is None:
        return None

    try:
        il = InvoiceService.upsert_water_invoice_line_from_reading(mr, billing_month_date=mr.reading_date)
        if il:
            logger.info("💧 Added Water Line %s to Invoice for Reading %s", il.pk, reading_id)
            return il.pk
        return None
    except Exception:
        logger.exception("Error while processing MeterReading %s", reading_id)
        raise
    
# ---------------------------------------------------------
# TASK 4: SMS NOTIFICATIONS
# ---------------------------------------------------------
@shared_task
def send_invoice_sms_task(invoice_id):
    """
    Async task to send invoice notification.
    Retries handled by MobileSasaAPI logic or explicit task retries.
    """
    from apps.tenant_management.comm.sms_utils import send_invoice_notification
    
    try:
        invoice = Invoice.objects.get(pk=invoice_id)
        
        # Guard: Don't send if it reverted to Draft
        if invoice.status != Invoice.STATUS_FINALIZED:
            logger.info(f"Skipping SMS for Invoice #{invoice_id}: Status is {invoice.status}")
            return "Skipped (Not Finalized)"
        
        success = send_invoice_notification(invoice)
        return "Sent" if success else "Failed"

    except Invoice.DoesNotExist:
        logger.error(f"Invoice #{invoice_id} not found during SMS task")
        return "Invoice Not Found"
    except Exception as e:
        logger.exception(f"Error sending SMS for Invoice #{invoice_id}: {e}")
        return "Error"
    
    

    
@shared_task
def send_bulk_sms_task(invoice_ids, mode):
    """
    Process a list of invoice IDs for bulk sending.
    This handles both 'New' and 'Reminder' modes.
    """
    logger.info(f"Starting Bulk SMS Task: {len(invoice_ids)} invoices, Mode: {mode}")
    
    invoices = Invoice.objects.filter(id__in=invoice_ids).select_related('tenant', 'tenant__property')
    api = MobileSasaAPI()
    
    count = 0
    for invoice in invoices:
        try:
            tenant = invoice.tenant
            if not tenant.phone_number: continue
            
            # Construct message based on mode
            if mode == 'new':
                 # Re-use standard notification function (it logs too)
                 success = send_invoice_notification(invoice)
            else:
                 # Construct Reminder Message
                 msg = (
                    f"REMINDER: Dear {tenant.full_name}, outstanding balance of KES {invoice.balance:,.0f} "
                    f"for {tenant.property.name} (Inv #{invoice.id}) is overdue. Please pay via M-Pesa."
                 )
                 success = api.send_single_sms(tenant, msg)
            
            if success: count += 1
            
        except Exception as e:
            logger.error(f"Failed to send bulk SMS to {invoice.id}: {e}")
            
    logger.info(f"Bulk SMS Task Complete. Sent: {count}")
    return f"Sent {count} messages"