# apps/tenant_management/tests/test_tenant_billing.py
import os
import django
import threading
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from django.db import transaction

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "GGI.settings")
django.setup()

from apps.tenant_management.models import (
    Tenant, Property, Unit, Lease, Invoice, InvoiceLine, WaterCompany, MeterReading
)
from apps.tenant_management.billings.services import (
    get_or_create_monthly_invoice,
    upsert_rent_invoice_line_for_lease,
    upsert_water_invoice_line_from_reading,
)


class MonthlyInvoiceFactoryTests(TestCase):
    def setUp(self):
        # Property + Water company
        self.water_company = WaterCompany.objects.create(name="Default Water Co.")
        self.property = Property.objects.create(
            name="Test Property",
            location="Test Location",
            water_policy=Property.SHARED,
            water_company=self.water_company,
        )
        self.unit = Unit.objects.create(
            property=self.property,
            unit_number="A1",
            rent_amount=Decimal("1000.00"),
        )
        self.tenant = Tenant.objects.create(
            full_name="Jane Doe",
            phone_number="0711222333",
            national_id="11223344",
            property=self.property,
        )
        self.lease = Lease.objects.create(
            tenant=self.tenant,
            unit=self.unit,
            start_date=timezone.now().date(),
            deposit_amount=Decimal("500.00"),
            is_active=True,
        )

    def test_get_or_create_creates_invoice_once(self):
        ref_date = timezone.now().date()

        inv1 = get_or_create_monthly_invoice(self.tenant, ref_date)
        inv2 = get_or_create_monthly_invoice(self.tenant, ref_date)

        self.assertEqual(inv1.id, inv2.id)
        self.assertEqual(Invoice.objects.count(), 1)

    def test_rent_and_water_share_same_invoice(self):
        ref_date = timezone.now().date()

        rent_invoice = upsert_rent_invoice_line_for_lease(self.lease, ref_date)

        reading = MeterReading.objects.create(
            unit=self.unit,
            reading_date=ref_date,
            usage=Decimal("10.00"),
            rate_per_cubic_meter=Decimal("50.00"),
        )
        water_line = upsert_water_invoice_line_from_reading(reading)

        self.assertIsNotNone(water_line)
        self.assertEqual(rent_invoice.id, water_line.invoice.id)
        self.assertEqual(Invoice.objects.filter(tenant=self.tenant).count(), 1)
        self.assertEqual(rent_invoice.lines.count(), 2)  # rent + water

    def test_invoice_is_unique_per_month(self):
        today = timezone.now().date()
        next_month = (today.replace(day=1) + timezone.timedelta(days=32)).replace(day=1)

        inv1 = get_or_create_monthly_invoice(self.tenant, today)
        inv2 = get_or_create_monthly_invoice(self.tenant, next_month)

        self.assertNotEqual(inv1.id, inv2.id)
        self.assertEqual(Invoice.objects.count(), 2)


# apps/tenant_management/tests/test_tenant_billing.py
from django.test import TransactionTestCase

class MonthlyInvoiceConcurrencyTests(TransactionTestCase):
    reset_sequences = True  # so PKs are stable across threads

    def setUp(self):
        self.water_company = WaterCompany.objects.create(name="Concurrent Water Co.")
        self.property = Property.objects.create(
            name="Concurrent Property",
            location="Concurrent Location",
            water_policy=Property.SHARED,
            water_company=self.water_company,
        )
        self.unit = Unit.objects.create(
            property=self.property,
            unit_number="C1",
            rent_amount=Decimal("2000.00"),
        )
        self.tenant = Tenant.objects.create(
            full_name="Concurrent User",
            phone_number="0711999888",
            national_id="99887766",
            property=self.property,
        )
        self.lease = Lease.objects.create(
            tenant=self.tenant,
            unit=self.unit,
            start_date=timezone.now().date(),
            deposit_amount=Decimal("1000.00"),
            is_active=True,
        )

    def _worker(self, results, idx, ref_date):
        inv = get_or_create_monthly_invoice(self.tenant, ref_date)
        results[idx] = inv.id

    def test_concurrent_double_threads(self):
        ref_date = timezone.now().date()
        results = [None, None]

        t1 = threading.Thread(target=self._worker, args=(results, 0, ref_date))
        t2 = threading.Thread(target=self._worker, args=(results, 1, ref_date))
        t1.start(); t2.start(); t1.join(); t2.join()

        self.assertEqual(len(set(results)), 1)
        self.assertEqual(Invoice.objects.count(), 1)

    def test_concurrent_stress_ten_threads(self):
        ref_date = timezone.now().date()
        num_threads = 10
        results = [None] * num_threads
        threads = [
            threading.Thread(target=self._worker, args=(results, i, ref_date))
            for i in range(num_threads)
        ]
        for t in threads: t.start()
        for t in threads: t.join()

        self.assertEqual(len(set(results)), 1)
        self.assertEqual(Invoice.objects.count(), 1)
