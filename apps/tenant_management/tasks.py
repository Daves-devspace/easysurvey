import logging
from celery import shared_task
from django.utils import timezone
from datetime import date

# Import the new Service Orchestrators
from apps.tenant_management.services.billing_cycle_service import BillingCycleService
from apps.tenant_management.services.invoice_service import InvoiceService
from apps.tenant_management.models import MeterReading

logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# TASK 1: MONTHLY RENT ROLL (Schedule: 1st of Month)
# ---------------------------------------------------------
@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def generate_monthly_invoices(self, run_date: str | None = None):
    """
    STEP 1 OF WORKFLOW: Rent Roll Generation.
    
    Should be scheduled via Celery Beat to run on the 1st of every month.
    Generates invoices with Rent + Auto-applies Credits.
    Status will be DRAFT or PENDING.

    Args:
        run_date: optional ISO string (YYYY-MM-DD) for deterministic testing.
    """
    ref_date = date.fromisoformat(run_date) if run_date else timezone.now().date()
    logger.info("🚀 Starting Monthly Rent Roll for date=%s", ref_date)

    try:
        # Delegate to the new Orchestrator
        result = BillingCycleService.generate_rent_roll(target_date=ref_date)
        
        logger.info(
            "✅ Rent Roll Complete. Created=%s, Errors=%s",
            result["created"], result["errors"]
        )
        return result
    except Exception:
        logger.exception("❌ Failed during Rent Roll generation for %s", ref_date)
        raise


# ---------------------------------------------------------
# TASK 2: DAILY FINALIZATION (Schedule: Daily at ~8:00 AM)
# ---------------------------------------------------------
@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def finalize_daily_invoices(self):
    """
    STEP 2 OF WORKFLOW: Daily Finalization Check.
    
    Should be scheduled via Celery Beat to run EVERY DAY.
    Checks if today is the 'billing_day' for any property.
    If so, it checks if invoices are ready (Rent + Water) and marks them FINALIZED.
    """
    today = timezone.now().date()
    logger.info("🔄 Running Daily Invoice Finalization Check for %s", today)

    try:
        # Delegate to the new Orchestrator
        BillingCycleService.process_billing_day(specific_date=today)
        
        logger.info("✅ Daily Finalization Check Complete.")
        return True
    except Exception:
        logger.exception("❌ Failed during Daily Finalization Check")
        raise


# ---------------------------------------------------------
# TASK 3: EVENT DRIVEN (Triggered on Save)
# ---------------------------------------------------------
@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def process_new_meter_reading(self, reading_id: int):
    """
    Event-Driven Task: Triggered when a MeterReading is saved.
    Updates the existing Pending Invoice with the water charge.
    """
    try:
        mr = MeterReading.objects.select_related(
            'unit', 'unit__property'
        ).get(pk=reading_id)
    except MeterReading.DoesNotExist:
        logger.warning("⚠️ MeterReading %s not found (probably deleted). Skipping.", reading_id)
        return None

    if mr.current_reading is None:
        logger.debug("Reading %s has no current_reading; skipping", reading_id)
        return None

    try:
        # Upsert the water line to the pending invoice
        # We pass reading_date to ensure we hit the correct invoice period
        il = InvoiceService.upsert_water_invoice_line_from_reading(
            mr, 
            billing_month_date=mr.reading_date
        )
        
        if il:
            logger.info("💧 Added Water Line %s to Invoice for Reading %s", il.pk, reading_id)
            return il.pk

        logger.debug("No invoice line created for Reading %s (Check lease status)", reading_id)
        return None
    except Exception:
        logger.exception("Error while processing MeterReading %s", reading_id)
        raise