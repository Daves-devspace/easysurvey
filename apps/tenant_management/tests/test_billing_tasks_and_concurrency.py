# apps/tenant_management/tests/test_billing_tasks_and_concurrency.py
import logging
import threading
import unittest
from decimal import Decimal
from datetime import date

from django.test import TransactionTestCase
from django.db.models import Sum
from django import db
from django.conf import settings

from apps.tenant_management.models import (
    Property, WaterCompany, Unit, Tenant, Lease,
    MeterReading, Invoice, InvoiceLine, WaterRate
)
from apps.tenant_management.billings.services import (
    upsert_rent_invoice_line_for_lease,
    upsert_water_invoice_line_from_reading,
)
from apps.tenant_management.tasks import process_new_meter_reading

logger = logging.getLogger("tests.billing_tasks_concurrency")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(levelname)s %(name)s: %(message)s')
    ch.setFormatter(formatter)
    logger.addHandler(ch)


class BillingTasksAndConcurrencyTests(TransactionTestCase):
    """
    Tests for Celery/task skipping behavior and a simple concurrency simulation
    for upserting water invoice lines.
    """

    reset_sequences = True  # helpful for deterministic PKs in some DBs

    def setUp(self):
        # Shared fixtures for tests
        self.wc = WaterCompany.objects.create(name="WC")
        self.prop = Property.objects.create(
            name="Prop",
            location="Loc",
            water_company=self.wc,
            billing_day=1,
            water_policy=Property.METER
        )

        # Active water rate (1.00 per unit)
        WaterRate.objects.create(
            water_company=self.wc,
            rate_per_cubic_meter=Decimal("1.00"),
            effective_from=date(2025, 1, 1),
            is_active=True,
        )

        self.unit = Unit.objects.create(
            property=self.prop,
            unit_number="101",
            rent_amount=Decimal("1000.00")
        )

        self.tenant = Tenant.objects.create(
            property=self.prop,
            full_name="Alice",
            phone_number="700",
            national_id="ID1"
        )

        self.lease = Lease.objects.create(
            tenant=self.tenant,
            unit=self.unit,
            start_date=date(2025, 1, 1),
            deposit_amount=Decimal("500.00"),
        )

    def test_process_new_meter_reading_skips_incomplete(self):
        """
        Calling the Celery task directly should early-return when reading.current_reading is None.
        """
        ref = date(2025, 2, 1)
        # Ensure rent upsert creates invoice + placeholder
        invoice = upsert_rent_invoice_line_for_lease(self.lease, billing_date=ref)

        mr = MeterReading.objects.create(
            unit=self.unit,
            previous_reading=Decimal("4.0"),
            current_reading=None,
            reading_date=ref
        )

        # Use Celery apply() to execute synchronously in-process and avoid bind/self issues
        res = process_new_meter_reading.apply(args=(mr.pk,)).get()
        self.assertIsNone(res)
        invoice.refresh_from_db()
        # placeholder still present, no meter_reading attached
        self.assertTrue(invoice.lines.filter(line_type=InvoiceLine.LINE_WATER, meter_reading__isnull=True).exists())

    @unittest.skipIf(
        settings.DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3',
        "SQLite doesn't support transactional row-level locking required for this concurrency test"
    )
    def test_concurrent_upserts_for_same_invoice(self):
        """
        Simulate two workers concurrently processing readings that map to the same tenant/invoice.
        This exercises select_for_update() locking in the service.
        """
        # create a second unit & lease on same tenant so both upserts touch same tenant/invoice
        unit2 = Unit.objects.create(property=self.prop, unit_number="102", rent_amount=Decimal("500.00"))
        lease2 = Lease.objects.create(tenant=self.tenant, unit=unit2, start_date=date(2025, 1, 1), deposit_amount=Decimal("200.00"))

        ref = date(2025, 2, 1)
        # ensure invoice/placeholders exist for both leases
        inv = upsert_rent_invoice_line_for_lease(self.lease, billing_date=ref)
        upsert_rent_invoice_line_for_lease(lease2, billing_date=ref)

        # create two readings in the same billing period
        mr1 = MeterReading.objects.create(unit=self.unit, previous_reading=Decimal("0.0"), current_reading=Decimal("5.0"), reading_date=ref)
        mr2 = MeterReading.objects.create(unit=unit2, previous_reading=Decimal("0.0"), current_reading=Decimal("3.0"), reading_date=ref)

        exceptions = []

        def worker(reading_pk):
            try:
                # each thread must close old connections so Django opens a fresh connection per worker
                db.connections.close_all()
                reading = MeterReading.objects.get(pk=reading_pk)
                upsert_water_invoice_line_from_reading(reading)
            except Exception as e:
                exceptions.append(e)

        t1 = threading.Thread(target=worker, args=(mr1.pk,))
        t2 = threading.Thread(target=worker, args=(mr2.pk,))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # close connections after threads finish to avoid leftover sessions during DB teardown
        db.connections.close_all()

        # re-raise if any thread errored
        if exceptions:
            raise exceptions[0]

        # Refresh invoice and check totals are consistent
        inv.refresh_from_db()
        total_agg = inv.lines.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

        # Recompute invoice total via model method and assert it equals the aggregated lines
        inv.recalc_total(save=True)
        inv.refresh_from_db()
        self.assertEqual(inv.total_amount, total_agg)
        
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        db.connections.close_all()
