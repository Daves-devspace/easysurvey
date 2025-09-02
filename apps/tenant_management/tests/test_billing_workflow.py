# apps/tenant_management/tests/test_billing_workflow.py
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from apps.tenant_management.models import (
    WaterCompany, Property, Unit, Tenant, Lease, WaterRate,
    MeterReading, Invoice, InvoiceLine, Payment, Deposit, TenantBalance, LedgerEntry
)
from apps.tenant_management.signals import apply_payment_safe
from apps.tenant_management.billings.services import q

class TenantManagementFullFlowTest(TestCase):

    def setUp(self):
        # Water company & rate
        self.water_company = WaterCompany.objects.create(name="Aqua Ltd")
        self.water_rate = WaterRate.objects.create(
            water_company=self.water_company,
            rate_per_cubic_meter=Decimal("5.00"),
            effective_from=timezone.now().date()
        )

        # Property & Unit
        self.property = Property.objects.create(
            name="Green Apartments",
            location="Nairobi",
            water_policy=Property.METER,
            water_company=self.water_company,
            billing_day=5
        )
        self.unit = Unit.objects.create(
            property=self.property,
            unit_number="101",
            rent_amount=Decimal("1000.00")
        )

        # Tenant & Lease
        self.tenant = Tenant.objects.create(
            property=self.property,
            full_name="John Doe",
            phone_number="0712345678",
            national_id="12345678"
        )
        self.lease = Lease.objects.create(
            tenant=self.tenant,
            unit=self.unit,
            start_date=timezone.now().date(),
            deposit_amount=Decimal("500.00")
        )

    def test_unit_occupancy_on_lease(self):
        self.assertTrue(Unit.objects.get(pk=self.unit.pk).is_occupied)
        self.lease.is_active = False
        self.lease.save()
        self.assertFalse(Unit.objects.get(pk=self.unit.pk).is_occupied)

    def test_rent_invoice_creation(self):
        from apps.tenant_management.billings.services import upsert_rent_invoice_line_for_lease
        invoice_line = upsert_rent_invoice_line_for_lease(self.lease)
        invoice = Invoice.objects.get(tenant=self.tenant)
        self.assertEqual(invoice.lines.filter(line_type=InvoiceLine.LINE_RENT).count(), 1)
        self.assertEqual(invoice.lines.filter(line_type=InvoiceLine.LINE_WATER).count(), 1)
        self.assertEqual(invoice.status, Invoice.STATUS_PENDING)

    def test_meter_reading_creates_water_line(self):
        reading = MeterReading.objects.create(
            unit=self.unit,
            previous_reading=Decimal("0"),
            current_reading=Decimal("10"),
            reading_date=timezone.now().date()
        )

        # Run the Celery task synchronously for testing
        from apps.tenant_management.tasks import process_new_meter_reading
        process_new_meter_reading(reading.pk)

        # Invoice line created
        invoice_line = InvoiceLine.objects.filter(meter_reading=reading).first()
        self.assertIsNotNone(invoice_line)
        self.assertEqual(invoice_line.amount, q(Decimal("10") * self.water_rate.rate_per_cubic_meter))

    def test_payment_applied_to_invoice_and_deposit(self):
        # Deposit starts at 200 (already held)
        deposit = Deposit.objects.get(lease=self.lease)
        deposit.amount_held = Decimal("200.00")
        deposit.save()

        # Create invoice 1000
        invoice = Invoice.objects.create(
            tenant=self.tenant,
            billing_period_start=timezone.now().date(),
            billing_period_end=timezone.now().date() + timezone.timedelta(days=30)
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            lease=self.lease,
            line_type=InvoiceLine.LINE_RENT,
            description="Rent",
            amount=Decimal("1000.00")
        )

        # Apply 1500 payment
        result = apply_payment_safe(self.tenant, Decimal("1500.00"), reference="ABC123")

        deposit.refresh_from_db()
        self.assertEqual(deposit.amount_held, Decimal("500.00"))  # deposit fully topped up

        invoice.refresh_from_db()
        self.assertTrue(invoice.is_paid)  # invoice fully paid
        self.assertEqual(invoice.balance, Decimal("0.00"))

        balance = TenantBalance.objects.get(tenant=self.tenant)
        # Remaining credit = 1500 - (deposit top-up 300 + invoice paid 1000) = 200 -> stored as tenant credit
        # Convention: negative = tenant credit
        self.assertEqual(balance.balance, Decimal("-200.00"))

        # No unallocated leftover (we persist leftover as tenant credit ledger entry)
        self.assertEqual(Decimal(result["unallocated"]), Decimal("0.00"))


    def test_overpayment_stored_as_credit(self):
        # Create invoice 1000
        invoice = Invoice.objects.create(
            tenant=self.tenant,
            billing_period_start=timezone.now().date(),
            billing_period_end=timezone.now().date() + timezone.timedelta(days=30)
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            lease=self.lease,
            line_type=InvoiceLine.LINE_RENT,
            description="Rent",
            amount=Decimal("1000.00")
        )

        # Apply 1700 payment
        result = apply_payment_safe(self.tenant, Decimal("1700.00"), reference="OVERPAY")

        deposit = Deposit.objects.get(lease=self.lease)
        self.assertEqual(deposit.amount_held, Decimal("500.00"))  # deposit fully applied (top-up)

        invoice.refresh_from_db()
        self.assertTrue(invoice.is_paid)  # invoice fully paid

        balance = TenantBalance.objects.get(tenant=self.tenant)
        # Remaining credit = 1700 - (deposit top-up 500 + invoice 1000) = 200 -> stored as tenant credit
        self.assertEqual(balance.balance, Decimal("-200.00"))

        self.assertEqual(Decimal(result["unallocated"]), Decimal("0.00"))


    def test_ledger_entries_created_for_deposit_payment(self):
        apply_payment_safe(self.tenant, Decimal("500.00"), reference="LEDGER")
        entries = LedgerEntry.objects.filter(tenant=self.tenant, entry_type=LedgerEntry.DEPOSIT)
        self.assertTrue(entries.exists())

    def test_deposit_refund_logging(self):
        deposit = Deposit.objects.get(lease=self.lease)
        deposit.amount_held = Decimal("500.00")
        deposit.save()
        self.lease.is_active = False
        self.lease.save()
        deposit.refresh_from_db()
        self.assertIsNone(deposit.refunded_at)
        self.assertEqual(deposit.amount_held, Decimal("500.00"))
