import pytest
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

from apps.tenant_management.models import (
    Property, Unit, Tenant, Lease, Invoice, InvoiceLine, 
    Payment, Deposit, MeterReading, WaterCompany, WaterRate
)
from apps.tenant_management.services.billing_service import BillingService
from apps.tenant_management.services.invoice_service import InvoiceService
from apps.tenant_management.services.payment_service import PaymentService
from apps.tenant_management.services.deposit_service import DepositService
from apps.tenant_management.exceptions import PaymentProcessingError, InvalidTenantError

@pytest.mark.django_db
class TestBillingService(TestCase):
    """Test cases for BillingService."""
    
    def setUp(self):
        # Create test data
        self.water_company = WaterCompany.objects.create(
            name="Test Water Co",
            contact_info="test@example.com"
        )
        
        self.property = Property.objects.create(
            name="Test Property",
            location="Test Location",
            water_policy="meter",
            water_company=self.water_company,
            billing_day=5
        )
        
        self.unit = Unit.objects.create(
            property=self.property,
            unit_number="A101",
            rent_amount=Decimal('15000.00'),
            is_occupied=True
        )
        
        self.tenant = Tenant.objects.create(
            property=self.property,
            full_name="John Doe",
            phone_number="1234567890",
            national_id="123456789"
        )
        
        self.lease = Lease.objects.create(
            tenant=self.tenant,
            unit=self.unit,
            start_date=date(2025, 1, 1),
            deposit_amount=Decimal('30000.00'),
            is_active=True
        )
        
        # Create water rate
        self.water_rate = WaterRate.objects.create(
            water_company=self.water_company,
            rate_per_cubic_meter=Decimal('50.00'),
            effective_from=date(2025, 1, 1),
            is_active=True
        )
    
    def test_get_or_create_monthly_invoice(self):
        """Test getting or creating a monthly invoice."""
        billing_date = date(2025, 1, 15)
        
        # First call should create the invoice
        invoice1 = BillingService.get_or_create_monthly_invoice(self.tenant, billing_date)
        self.assertIsNotNone(invoice1)
        self.assertEqual(invoice1.tenant, self.tenant)
        
        # Second call should return the same invoice
        invoice2 = BillingService.get_or_create_monthly_invoice(self.tenant, billing_date)
        self.assertEqual(invoice1.id, invoice2.id)
    
    def test_get_or_create_invoice_for_period(self):
        """Test getting or creating an invoice for a specific period."""
        start_date = date(2025, 1, 5)
        end_date = date(2025, 2, 5)
        
        # First call should create the invoice
        invoice1 = BillingService.get_or_create_invoice_for_period(self.tenant, start_date, end_date)
        self.assertIsNotNone(invoice1)
        self.assertEqual(invoice1.tenant, self.tenant)
        self.assertEqual(invoice1.billing_period_start, start_date)
        self.assertEqual(invoice1.billing_period_end, end_date)
        
        # Second call should return the same invoice
        invoice2 = BillingService.get_or_create_invoice_for_period(self.tenant, start_date, end_date)
        self.assertEqual(invoice1.id, invoice2.id)

@pytest.mark.django_db
class TestInvoiceService(TestCase):
    """Test cases for InvoiceService."""
    
    def setUp(self):
        # Create test data (same as above)
        self.water_company = WaterCompany.objects.create(
            name="Test Water Co",
            contact_info="test@example.com"
        )
        
        self.property = Property.objects.create(
            name="Test Property",
            location="Test Location",
            water_policy="meter",
            water_company=self.water_company,
            billing_day=5
        )
        
        self.unit = Unit.objects.create(
            property=self.property,
            unit_number="A101",
            rent_amount=Decimal('15000.00'),
            is_occupied=True
        )
        
        self.tenant = Tenant.objects.create(
            property=self.property,
            full_name="John Doe",
            phone_number="1234567890",
            national_id="123456789"
        )
        
        self.lease = Lease.objects.create(
            tenant=self.tenant,
            unit=self.unit,
            start_date=date(2025, 1, 1),
            deposit_amount=Decimal('30000.00'),
            is_active=True
        )
        
        # Create water rate
        self.water_rate = WaterRate.objects.create(
            water_company=self.water_company,
            rate_per_cubic_meter=Decimal('50.00'),
            effective_from=date(2025, 1, 1),
            is_active=True
        )
    
    def test_upsert_rent_invoice_line_for_lease(self):
        """Test creating/updating rent invoice lines for a lease."""
        billing_date = date(2025, 1, 15)
        
        # Create invoice with rent line
        invoice = InvoiceService.upsert_rent_invoice_line_for_lease(self.lease, billing_date)
        
        # Check that invoice was created
        self.assertIsNotNone(invoice)
        
        # Check that rent line was created
        rent_lines = invoice.lines.filter(line_type=InvoiceLine.LINE_RENT)
        self.assertEqual(rent_lines.count(), 1)
        self.assertEqual(rent_lines.first().amount, Decimal('15000.00'))
        
        # Check that deposit line was created (first invoice)
        deposit_lines = invoice.lines.filter(line_type=InvoiceLine.LINE_DEPOSIT)
        self.assertEqual(deposit_lines.count(), 1)
        self.assertEqual(deposit_lines.first().amount, Decimal('30000.00'))

@pytest.mark.django_db
class TestPaymentService(TestCase):
    """Test cases for PaymentService."""
    
    def setUp(self):
        # Create test data (same as above)
        self.water_company = WaterCompany.objects.create(
            name="Test Water Co",
            contact_info="test@example.com"
        )
        
        self.property = Property.objects.create(
            name="Test Property",
            location="Test Location",
            water_policy="meter",
            water_company=self.water_company,
            billing_day=5
        )
        
        self.unit = Unit.objects.create(
            property=self.property,
            unit_number="A101",
            rent_amount=Decimal('15000.00'),
            is_occupied=True
        )
        
        self.tenant = Tenant.objects.create(
            property=self.property,
            full_name="John Doe",
            phone_number="1234567890",
            national_id="123456789"
        )
        
        self.lease = Lease.objects.create(
            tenant=self.tenant,
            unit=self.unit,
            start_date=date(2025, 1, 1),
            deposit_amount=Decimal('30000.00'),
            is_active=True
        )
        
        # Create an invoice
        self.invoice = Invoice.objects.create(
            tenant=self.tenant,
            billing_period_start=date(2025, 1, 5),
            billing_period_end=date(2025, 2, 5),
            status=Invoice.STATUS_FINALIZED,
            total_amount=Decimal('45000.00')  # Rent + deposit
        )
        
        # Create rent and deposit lines
        InvoiceLine.objects.create(
            invoice=self.invoice,
            lease=self.lease,
            line_type=InvoiceLine.LINE_RENT,
            description="Monthly Rent",
            amount=Decimal('15000.00')
        )
        
        InvoiceLine.objects.create(
            invoice=self.invoice,
            lease=self.lease,
            line_type=InvoiceLine.LINE_DEPOSIT,
            description="Security Deposit",
            amount=Decimal('30000.00')
        )
    
    def test_process_payment(self):
        """Test processing a payment."""
        result = PaymentService.process_payment(
            tenant=self.tenant,
            amount=Decimal('20000.00'),
            reference="Test Payment",
            method="Mpesa"
        )
        
        # Check that payment was processed
        self.assertIn("applied_to_invoices", result)
        self.assertIn("applied_to_deposit", result)
        
        # Check that payment record was created
        payments = Payment.objects.filter(tenant=self.tenant)
        self.assertEqual(payments.count(), 1)
        self.assertEqual(payments.first().amount, Decimal('20000.00'))
    
    def test_apply_credit_to_invoice(self):
        """Test applying credit to an invoice."""
        # First create a credit payment
        Payment.objects.create(
            tenant=self.tenant,
            invoice=None,
            amount=Decimal('10000.00'),
            method="Credit",
            reference="Test Credit",
            payment_type="CREDIT"
        )
        
        # Then apply credit to invoice
        result = PaymentService.apply_credit_to_invoice(self.tenant, self.invoice)
        
        # Check that credit was applied
        self.assertIn("applied_to_invoices", result)
        self.assertGreater(Decimal(result["applied_to_invoices"]), Decimal('0.00'))

@pytest.mark.django_db
class TestDepositService(TestCase):
    """Test cases for DepositService."""
    
    def setUp(self):
        # Create test data (same as above)
        self.water_company = WaterCompany.objects.create(
            name="Test Water Co",
            contact_info="test@example.com"
        )
        
        self.property = Property.objects.create(
            name="Test Property",
            location="Test Location",
            water_policy="meter",
            water_company=self.water_company,
            billing_day=5
        )
        
        self.unit = Unit.objects.create(
            property=self.property,
            unit_number="A101",
            rent_amount=Decimal('15000.00'),
            is_occupied=True
        )
        
        self.tenant = Tenant.objects.create(
            property=self.property,
            full_name="John Doe",
            phone_number="1234567890",
            national_id="123456789"
        )
        
        self.lease = Lease.objects.create(
            tenant=self.tenant,
            unit=self.unit,
            start_date=date(2025, 1, 1),
            deposit_amount=Decimal('30000.00'),
            is_active=True
        )
        
        # Create a deposit
        self.deposit = Deposit.objects.create(
            lease=self.lease,
            tenant=self.tenant,
            amount=Decimal('30000.00'),
            amount_held=Decimal('15000.00'),
            paid_at=timezone.now()
        )
        
        # Create an invoice
        self.invoice = Invoice.objects.create(
            tenant=self.tenant,
            billing_period_start=date(2025, 1, 5),
            billing_period_end=date(2025, 2, 5),
            status=Invoice.STATUS_FINALIZED,
            total_amount=Decimal('15000.00')  # Rent only
        )
        
        # Create rent line
        InvoiceLine.objects.create(
            invoice=self.invoice,
            lease=self.lease,
            line_type=InvoiceLine.LINE_RENT,
            description="Monthly Rent",
            amount=Decimal('15000.00')
        )
    
    def test_apply_deposit_to_invoice(self):
        """Test applying deposit to an invoice."""
        result = DepositService.apply_deposit_to_invoice(
            deposit=self.deposit,
            invoice=self.invoice,
            amount=Decimal('5000.00')
        )
        
        # Check that deposit was applied
        self.assertIsNotNone(result)
        
        # Check that deposit amount_held was reduced
        self.deposit.refresh_from_db()
        self.assertEqual(self.deposit.amount_held, Decimal('10000.00'))
        
        # Check that ledger entry was created
        from apps.tenant_management.models import LedgerEntry
        ledger_entries = LedgerEntry.objects.filter(deposit=self.deposit)
        self.assertEqual(ledger_entries.count(), 1)
        
        # Check that negative invoice line was created
        negative_lines = self.invoice.lines.filter(amount__lt=0)
        self.assertEqual(negative_lines.count(), 1)
        self.assertEqual(negative_lines.first().amount, Decimal('-5000.00'))
    
    def test_refund_deposit(self):
        """Test refunding a deposit."""
        result = DepositService.refund_deposit(
            deposit=self.deposit,
            amount=Decimal('5000.00')
        )
        
        # Check that deposit was refunded
        self.assertIsNotNone(result)
        
        # Check that deposit amount_held was reduced
        self.deposit.refresh_from_db()
        self.assertEqual(self.deposit.amount_held, Decimal('10000.00'))
        self.assertEqual(self.deposit.refunded_amount, Decimal('5000.00'))
        
        # Check that ledger entry was created
        from apps.tenant_management.models import LedgerEntry
        ledger_entries = LedgerEntry.objects.filter(deposit=self.deposit)
        self.assertEqual(ledger_entries.count(), 1)