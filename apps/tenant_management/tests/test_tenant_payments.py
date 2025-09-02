# tests/test_tenant_billing.py
import os
import django
from decimal import Decimal
from threading import Thread
from django.utils import timezone
from django.db import transaction

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "GGI.settings")
django.setup()
# apps/tenant_management/tests/test_tenant_payments.py
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from apps.tenant_management.models import (
    Tenant, Property, Unit, Lease, Invoice, InvoiceLine,
    Payment, Deposit, LedgerEntry, TenantBalance,WaterCompany
)
from apps.tenant_management.signals import apply_payment_safe, _apply_credit_and_deposit

class TenantPaymentTests(TestCase):
    def setUp(self):
        # Create Property & WaterCompany
        self.water_company = WaterCompany.objects.create(name="Default Water Co.")
        self.property = Property.objects.create(
            name="Test Property",
            location="Test Location",
            water_policy=Property.SHARED,
            water_company=self.water_company
        )
        self.unit = Unit.objects.create(
            property=self.property,
            unit_number="A1",
            rent_amount=Decimal("1000.00")
        )
        self.tenant = Tenant.objects.create(
            full_name="John Doe",
            phone_number="0712345678",
            national_id="12345678",
            property=self.property
        )
        self.lease = Lease.objects.create(
            tenant=self.tenant,
            unit=self.unit,
            start_date=timezone.now().date(),
            deposit_amount=Decimal("500.00"),
            is_active=True
        )

        # Create deposit manually to avoid auto-create conflict
        self.deposit = Deposit.objects.create(
            lease=self.lease,
            tenant=self.tenant,
            amount=self.lease.deposit_amount,
            amount_held=Decimal("0.00")
        )

        # Ensure tenant balance starts at 0
        TenantBalance.objects.update_or_create(
            tenant=self.tenant, defaults={"balance": Decimal("0.00")}
        )

    def test_apply_payment_full_invoice(self):
        invoice = Invoice.objects.create(
            tenant=self.tenant,
            billing_period_start=timezone.now().date(),
            billing_period_end=timezone.now().date()
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            lease=self.lease,
            line_type=InvoiceLine.LINE_RENT,
            description="Monthly Rent",
            amount=Decimal("400.00")
        )

        result = apply_payment_safe(
            tenant=self.tenant,
            payment_amount=Decimal("500.00"),
            reference="Mpesa456",
            method="Mpesa"
        )

        self.deposit.refresh_from_db()
        invoice.refresh_from_db()

        self.assertEqual(invoice.balance, Decimal("0.00"))
        self.assertTrue(invoice.is_paid)
        self.assertEqual(self.deposit.amount_held, Decimal("100.00"))
        self.assertEqual(result["applied_to_invoices"], "400.00")
        self.assertEqual(result["applied_to_deposit"], "100.00")
        self.assertEqual(result["unallocated"], "0.00")

    def test_apply_payment_topup_deposit_then_invoice(self):
        invoice = Invoice.objects.create(
            tenant=self.tenant,
            billing_period_start=timezone.now().date(),
            billing_period_end=timezone.now().date()
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            lease=self.lease,
            line_type=InvoiceLine.LINE_RENT,
            description="Monthly Rent",
            amount=Decimal("1000.00")
        )

        result = apply_payment_safe(
            tenant=self.tenant,
            payment_amount=Decimal("1200.00"),
            reference="Mpesa123",
            method="Mpesa",
            apply_to_deposit=True
        )

        self.deposit.refresh_from_db()
        invoice.refresh_from_db()
        tenant_balance = TenantBalance.objects.get(tenant=self.tenant)

        self.assertEqual(invoice.balance, Decimal("0.00"))
        self.assertTrue(invoice.is_paid)
        self.assertEqual(self.deposit.amount_held, Decimal("200.00"))
        self.assertEqual(tenant_balance.balance, Decimal("0.00"))
        self.assertEqual(result["applied_to_invoices"], "1000.00")
        self.assertEqual(result["applied_to_deposit"], "200.00")
        self.assertEqual(result["unallocated"], "0.00")

    def test_auto_apply_credit_to_invoice(self):
        # Create tenant credit
        LedgerEntry.objects.create(
            tenant=self.tenant,
            entry_type=LedgerEntry.RENT,
            credit=Decimal("300.00"),
            description="Overpayment"
        )
        # Recalculate balance
        TenantBalance.recalc_for_tenant(self.tenant)
        tenant_balance = TenantBalance.objects.get(tenant=self.tenant)
        self.assertEqual(tenant_balance.balance, Decimal("-300.00"))

        # Create invoice
        invoice = Invoice.objects.create(
            tenant=self.tenant,
            billing_period_start=timezone.now().date(),
            billing_period_end=timezone.now().date()
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            lease=self.lease,
            line_type=InvoiceLine.LINE_RENT,
            description="Monthly Rent",
            amount=Decimal("200.00")
        )

        # Use internal helper to apply tenant credit
        result = _apply_credit_and_deposit(
            tenant=self.tenant,
            payment_amount=None,
            invoice=invoice
        )

        invoice.refresh_from_db()
        tenant_balance.refresh_from_db()

        self.assertEqual(invoice.balance, Decimal("0.00"))
        self.assertTrue(invoice.is_paid)
        self.assertEqual(tenant_balance.balance, Decimal("-100.00"))
        self.assertEqual(result["applied_to_invoices"], "200.00")
        self.assertEqual(result["applied_to_deposit"], "0.00")
        self.assertIsNone(result["unallocated"])

    def test_partial_payment_creates_overpayment_credit(self):
        invoice = Invoice.objects.create(
            tenant=self.tenant,
            billing_period_start=timezone.now().date(),
            billing_period_end=timezone.now().date()
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            lease=self.lease,
            line_type=InvoiceLine.LINE_RENT,
            description="Monthly Rent",
            amount=Decimal("800.00")
        )

        result = apply_payment_safe(
            tenant=self.tenant,
            payment_amount=Decimal("1000.00"),
            reference="Mpesa789",
            method="Mpesa",
            apply_to_deposit=False  # Changed: Don't apply overpayment to deposit
        )

        invoice.refresh_from_db()
        tenant_balance = TenantBalance.objects.get(tenant=self.tenant)

        self.assertTrue(invoice.is_paid)
        self.assertEqual(invoice.balance, Decimal("0.00"))
        self.assertEqual(tenant_balance.balance, Decimal("-200.00"))
        self.assertEqual(result["applied_to_invoices"], "800.00")
        self.assertEqual(result["applied_to_deposit"], "0.00")
        self.assertEqual(result["unallocated"], "0.00")