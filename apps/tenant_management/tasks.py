# apps/tenant_management/billings/tasks.py
import logging
from celery import shared_task
from django.utils import timezone
from datetime import date

from apps.tenant_management.models import Lease, MeterReading
from apps.tenant_management.billings.services import (
    upsert_rent_invoice_line_for_lease,
    upsert_water_invoice_line_from_reading,
)

logger = logging.getLogger(__name__)


@shared_task
def generate_monthly_rent_invoices(run_date: str | None = None):
    """
    Celery task to create/update rent invoice lines for all active leases.
    - run_date: optional ISO string (YYYY-MM-DD) for deterministic testing; defaults to today.
    """
    ref_date = date.fromisoformat(run_date) if run_date else timezone.now().date()
    logger.info("🏠 Generating rent invoices for reference date=%s", ref_date)

    count = 0
    for lease in Lease.objects.filter(is_active=True).select_related("unit", "tenant"):
        invoice = upsert_rent_invoice_line_for_lease(lease, billing_date=ref_date)
        if invoice:
            count += 1
    logger.info("✅ Rent invoice task complete. Updated %s leases.", count)
    return count


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def process_new_meter_reading(self, reading_id: int):
    """
    Celery Task that delegates to the service layer.
    Retries safely if DB is locked or transient error occurs.
    """
    try:
        # fetch with related to avoid extra DB hits in service
        mr = MeterReading.objects.select_related(
            'unit', 'unit__property', 'unit__property__water_company'
        ).get(pk=reading_id)
    except MeterReading.DoesNotExist:
        logger.warning("⚠️ MeterReading %s not found (probably deleted). Skipping.", reading_id)
        return None

    # Skip incomplete readings (no current_reading yet)
    if mr.current_reading is None:
        logger.debug("process_new_meter_reading: reading %s has no current_reading; skipping", reading_id)
        return None

    try:
        il = upsert_water_invoice_line_from_reading(mr)
        if il:
            logger.info("💧 Processed MeterReading %s → InvoiceLine %s", reading_id, il.pk)
            return il.pk

        logger.debug("MeterReading %s did not result in invoice line (not latest/final)", reading_id)
        return None

    except Exception:
        # Let Celery retry for transient errors — but log full trace for debugging
        logger.exception("Error while processing MeterReading %s", reading_id)
        raise

