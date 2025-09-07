# apps/tenant_management/billings/tasks.py
import logging
from celery import shared_task
from django.utils import timezone
from datetime import date

from apps.tenant_management.billings.utils import (
    generate_monthly_invoices_for_all_leases,
)
from apps.tenant_management.billings.services import (
    upsert_water_invoice_line_from_reading,
)
from apps.tenant_management.models import MeterReading, InvoiceLine

logger = logging.getLogger(__name__)


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def generate_monthly_invoices(self, run_date: str | None = None):
    """
    Celery task: batch-generate fully finalized invoices (rent + water + credits)
    for all active leases in one go.

    Args:
        run_date: optional ISO string (YYYY-MM-DD) for deterministic testing;
                  defaults to today.

    Returns:
        dict: summary {created: X, updated: Y}
    """
    ref_date = date.fromisoformat(run_date) if run_date else timezone.now().date()
    logger.info("📄 Generating ALL invoices for reference date=%s", ref_date)

    try:
        result = generate_monthly_invoices_for_all_leases(ref_date)
        logger.info("✅ Monthly invoice task complete. Created=%s, Updated=%s",
                    result["created"], result["updated"])
        return result
    except Exception:
        logger.exception("❌ Failed while generating monthly invoices for %s", ref_date)
        raise
    
    
    

@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def process_new_meter_reading(self, reading_id: int):
    """
    Celery Task that delegates to the service layer.
    Now passes billing_month_date to ensure consistent billing period selection.
    """
    from apps.tenant_management.billings.services import upsert_water_invoice_line_from_reading

    try:
        mr = MeterReading.objects.select_related(
            'unit', 'unit__property', 'unit__property__water_company'
        ).get(pk=reading_id)
    except MeterReading.DoesNotExist:
        logger.warning("⚠️ MeterReading %s not found (probably deleted). Skipping.", reading_id)
        return None

    if mr.current_reading is None:
        logger.debug("process_new_meter_reading: reading %s has no current_reading; skipping", reading_id)
        return None

    try:
        # Pass the reading_date as billing_month_date to ensure consistency
        il = upsert_water_invoice_line_from_reading(mr, billing_month_date=mr.reading_date)
        if il:
            logger.info("💧 Processed MeterReading %s → InvoiceLine %s", reading_id, il.pk)
            return il.pk

        logger.debug("MeterReading %s did not result in invoice line (not latest/final)", reading_id)
        return None
    except Exception:
        logger.exception("Error while processing MeterReading %s", reading_id)
        raise



