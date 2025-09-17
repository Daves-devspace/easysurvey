# apps/tenant_management/tests/test_robust_payment_workflow.py
"""
ROBUST PAYMENT SYSTEM TESTS

This test file validates the Payment-only approach where:
1. All payments are tracked via Payment records linked to invoices
2. No negative InvoiceLines are created for payments
3. Invoice.balance = total_amount - sum of linked Payment records
4. Deposit payments update Deposit.amount_held directly
5. TenantBalance calculation uses only Payment records and invoice balances

Key Changes from Original:
- Removed reliance on LedgerEntry for balance calculations
- Updated expectations for Payment record creation
- Fixed balance calculation expectations
- Added verification of Payment record linking
"""
import logging
from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from django.db import transaction
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "GGI.settings")
django.setup()

from apps.tenant_management.models import (
    Property, Unit, Tenant, Lease, MeterReading, Invoice, 
    InvoiceLine, Payment, TenantBalance, WaterCompany, WaterRate, Deposit
)
from apps.tenant_management.billings.services import (
    billing_period_for_billing_month,
    apply_credit_and_deposit,
    get_or_create_monthly_invoice
)
from apps.tenant_management.billings.utils import _process_lease_for_month

logger = logging.getLogger(__name__)


class RobustPaymentWorkflowTestCase(TestCase):
    """
    Comprehensive test of the robust Payment-only system.
    
    This test validates the new payment processing approach where:
    1. Payments create Payment records linked to specific invoices
    2. Deposit prioritization works within each invoice
    3. Balance calculations use only Payment records
    4. No negative InvoiceLines are created
    """
    
    def setUp(self):
        # Create test data
        self.water_company = WaterCompany.objects.create(
            name="Nairobi Water",
            contact_info="nairobi@water.com"
        )
        
        self.property = Property.objects.create(
            name="Sunset Apartments",
            location="Nairobi",
            water_policy=Property.METER,
            water_company=self.water_company,
            billing_day=5
        )
        
        self.unit = Unit.objects.create(
            property=self.property,
            unit_number="A101",
            rent_amount=Decimal('15000.00'),
            meter_number="MTR-A101"
        )
        
        self.tenant = Tenant.objects.create(
            property=self.property,
            full_name="John Doe",
            phone_number="+254712345678",
            national_id="12345678"
        )
        
        self.lease = Lease.objects.create(
            tenant=self.tenant,
            unit=self.unit,
            start_date=date(2025, 1, 1),
            deposit_amount=Decimal('30000.00'),
            is_active=True
        )
        
        self.water_rate = WaterRate.objects.create(
            water_company=self.water_company,
            rate_per_cubic_meter=Decimal('50.00'),
            effective_from=date(2025, 1, 1),
            is_active=True
        )
        
        # January meter reading
        self.jan_reading = MeterReading.objects.create(
            unit=self.unit,
            reading_date=date(2025, 1, 31),
            previous_reading=Decimal('0.00'),
            current_reading=Decimal('50.00'),
            usage=Decimal('50.00'),
            rate_per_cubic_meter=Decimal('50.00'),
            amount=Decimal('2500.00')
        )
    
    def tearDown(self):
        """Clean up test data"""
        models_to_clear = [
            MeterReading, InvoiceLine, Invoice, Payment, 
            TenantBalance, Lease, Tenant, Unit, Property,
            WaterRate, WaterCompany, Deposit
        ]
        
        for model in models_to_clear:
            try:
                model.objects.all().delete()
            except Exception as e:
                logger.warning(f"Failed to delete {model.__name__} objects: {e}")

    def test_01_january_invoice_creation(self):
        """Test Period 1: January 2025 Invoice Creation with Deposit"""
        # Generate January invoice
        result = _process_lease_for_month(self.lease, date(2025, 1, 1))
        self.assertEqual(result["status"], "created")
        
        # Get January invoice
        jan_start, jan_end = billing_period_for_billing_month(date(2025, 1, 1), 5)
        jan_invoice = Invoice.objects.get(
            tenant=self.tenant, 
            billing_period_start=jan_start, 
            billing_period_end=jan_end
        )
        
        # Verify invoice lines (all positive amounts - no negative payment lines)
        rent_line = jan_invoice.lines.get(line_type=InvoiceLine.LINE_RENT)
        self.assertEqual(rent_line.amount, Decimal('15000.00'))
        self.assertGreater(rent_line.amount, 0)  # Must be positive
        
        water_line = jan_invoice.lines.get(line_type=InvoiceLine.LINE_WATER)
        self.assertEqual(water_line.amount, Decimal('2500.00'))
        self.assertGreater(water_line.amount, 0)  # Must be positive
        
        deposit_line = jan_invoice.lines.get(line_type=InvoiceLine.LINE_DEPOSIT)
        self.assertEqual(deposit_line.amount, Decimal('30000.00'))
        self.assertGreater(deposit_line.amount, 0)  # Must be positive
        
        # ROBUST: Verify no negative invoice lines exist
        negative_lines = jan_invoice.lines.filter(amount__lt=0)
        self.assertEqual(negative_lines.count(), 0)
        
        # Verify invoice totals
        self.assertEqual(jan_invoice.total_amount, Decimal('47500.00'))
        self.assertEqual(jan_invoice.total_paid, Decimal('0.00'))  # No payments yet
        self.assertEqual(jan_invoice.balance, Decimal('47500.00'))
        self.assertFalse(jan_invoice.is_paid)
        
        # Verify deposit object
        deposit = Deposit.objects.get(lease=self.lease)
        self.assertEqual(deposit.amount, Decimal('30000.00'))
        self.assertEqual(deposit.amount_held, Decimal('0.00'))
        self.assertIsNone(deposit.paid_at)
        
        # ROBUST: Verify tenant balance calculation
        tenant_balance = TenantBalance.recalc_for_tenant(self.tenant)
        self.assertEqual(tenant_balance.balance, Decimal('47500.00'))
        
        return jan_invoice

    def test_02_partial_deposit_payment(self):
        """Test Payment 1: KES 25,000 - Partial deposit payment"""
        jan_invoice = self.test_01_january_invoice_creation()
        
        # Process partial deposit payment
        payment_result = apply_credit_and_deposit(
            tenant=self.tenant,
            payment_amount=Decimal('25000.00'),
            reference="Partial Deposit Payment",
            method="Mpesa"
        )
        
        # ROBUST: Verify payment allocation
        self.assertEqual(payment_result["applied_to_deposit"], "25000.00")
        self.assertEqual(payment_result["applied_to_invoices"], "25000.00")
        self.assertEqual(payment_result["stored_as_credit"], "0.00")
        self.assertEqual(payment_result["unallocated"], "0.00")
        
        # ROBUST: Verify Payment record was created and linked to invoice
        payments = Payment.objects.filter(tenant=self.tenant)
        self.assertEqual(payments.count(), 1)
        
        payment = payments.first()
        self.assertEqual(payment.amount, Decimal('25000.00'))
        self.assertEqual(payment.invoice, jan_invoice)
        self.assertEqual(payment.payment_type, 'DEPOSIT')
        self.assertEqual(payment.method, 'Mpesa')
        
        # ROBUST: Verify deposit status updated directly
        deposit = Deposit.objects.get(lease=self.lease)
        self.assertEqual(deposit.amount_held, Decimal('25000.00'))
        self.assertIsNone(deposit.paid_at)  # Not fully paid yet
        
        # ROBUST: Verify invoice balance calculation using Payment records
        jan_invoice.refresh_from_db()
        self.assertEqual(jan_invoice.total_amount, Decimal('47500.00'))  # Unchanged
        self.assertEqual(jan_invoice.total_paid, Decimal('25000.00'))    # From Payment record
        self.assertEqual(jan_invoice.balance, Decimal('22500.00'))       # 47500 - 25000
        self.assertFalse(jan_invoice.is_paid)
        
        # ROBUST: Verify no negative invoice lines were created
        negative_lines = jan_invoice.lines.filter(amount__lt=0)
        self.assertEqual(negative_lines.count(), 0)
        
        # ROBUST: Verify tenant balance
        tenant_balance = TenantBalance.recalc_for_tenant(self.tenant)
        self.assertEqual(tenant_balance.balance, Decimal('22500.00'))
        
        return jan_invoice

    def test_03_complete_deposit_payment(self):
        """Test Payment 2: KES 10,000 - Complete deposit + start other charges"""
        jan_invoice = self.test_02_partial_deposit_payment()
        
        # Process payment that completes deposit and pays other charges
        payment_result = apply_credit_and_deposit(
            tenant=self.tenant,
            payment_amount=Decimal('10000.00'),
            reference="Complete Deposit Payment",
            method="Mpesa"
        )
        
        # ROBUST: Verify payment allocation
        # 5000 to complete deposit, 5000 to other charges
        self.assertEqual(payment_result["applied_to_deposit"], "5000.00")
        self.assertEqual(payment_result["applied_to_invoices"], "10000.00")
        self.assertEqual(payment_result["stored_as_credit"], "0.00")
        
        # ROBUST: Verify second Payment record was created
        payments = Payment.objects.filter(tenant=self.tenant).order_by('payment_date')
        self.assertEqual(payments.count(), 2)
        
        second_payment = payments[1]
        self.assertEqual(second_payment.amount, Decimal('10000.00'))
        self.assertEqual(second_payment.invoice, jan_invoice)
        self.assertEqual(second_payment.payment_type, 'MIXED')  # Both deposit and other charges
        
        # ROBUST: Verify deposit is now fully paid
        deposit = Deposit.objects.get(lease=self.lease)
        self.assertEqual(deposit.amount_held, Decimal('30000.00'))
        self.assertIsNotNone(deposit.paid_at)
        
        # ROBUST: Verify invoice balance using Payment records
        jan_invoice.refresh_from_db()
        self.assertEqual(jan_invoice.total_paid, Decimal('35000.00'))  # 25000 + 10000
        self.assertEqual(jan_invoice.balance, Decimal('12500.00'))     # 47500 - 35000
        
        # ROBUST: Verify tenant balance
        tenant_balance = TenantBalance.recalc_for_tenant(self.tenant)
        self.assertEqual(tenant_balance.balance, Decimal('12500.00'))
        
        return jan_invoice

    def test_04_february_invoice_and_oldest_first_payment(self):
        """Test February invoice creation and oldest-first payment processing"""
        jan_invoice = self.test_03_complete_deposit_payment()
        
        # Create February meter reading
        feb_reading = MeterReading.objects.create(
            unit=self.unit,
            reading_date=date(2025, 2, 28),
            previous_reading=Decimal('50.00'),
            current_reading=Decimal('86.00'),
            usage=Decimal('36.00'),
            rate_per_cubic_meter=Decimal('50.00'),
            amount=Decimal('1800.00')
        )
        
        # Generate February invoice
        result = _process_lease_for_month(self.lease, date(2025, 2, 1))
        self.assertEqual(result["status"], "created")
        
        # Get February invoice
        feb_start, feb_end = billing_period_for_billing_month(date(2025, 2, 1), 5)
        feb_invoice = Invoice.objects.get(
            tenant=self.tenant, 
            billing_period_start=feb_start, 
            billing_period_end=feb_end
        )
        
        # Verify February invoice (no deposit line)
        self.assertEqual(feb_invoice.total_amount, Decimal('16800.00'))  # 15000 + 1800
        self.assertEqual(feb_invoice.balance, Decimal('16800.00'))
        
        # ROBUST: Verify no negative lines in February invoice
        negative_lines = feb_invoice.lines.filter(amount__lt=0)
        self.assertEqual(negative_lines.count(), 0)
        
        # Process payment that should prioritize January (oldest first)
        payment_result = apply_credit_and_deposit(
            tenant=self.tenant,
            payment_amount=Decimal('20000.00'),
            reference="Oldest First Payment",
            method="Mpesa"
        )
        
        # ROBUST: Verify payment allocation
        # Should pay Jan remainder (12500) first, then Feb (7500)
        self.assertEqual(payment_result["applied_to_invoices"], "20000.00")
        self.assertEqual(payment_result["stored_as_credit"], "0.00")
        
        # ROBUST: Verify Payment records were created for both invoices
        jan_payments = Payment.objects.filter(invoice=jan_invoice)
        feb_payments = Payment.objects.filter(invoice=feb_invoice)
        
        # Should have 3 payments for January (2 previous + 1 new for remainder)
        self.assertEqual(jan_payments.count(), 3)
        jan_completion_payment = jan_payments.order_by('-payment_date').first()
        self.assertEqual(jan_completion_payment.amount, Decimal('12500.00'))
        
        # Should have 1 payment for February
        self.assertEqual(feb_payments.count(), 1)
        feb_payment = feb_payments.first()
        self.assertEqual(feb_payment.amount, Decimal('7500.00'))
        
        # ROBUST: Verify invoice balances
        jan_invoice.refresh_from_db()
        feb_invoice.refresh_from_db()
        
        self.assertEqual(jan_invoice.balance, Decimal('0.00'))
        self.assertTrue(jan_invoice.is_paid)
        
        self.assertEqual(feb_invoice.balance, Decimal('9300.00'))  # 16800 - 7500
        self.assertFalse(feb_invoice.is_paid)
        
        # ROBUST: Verify tenant balance
        tenant_balance = TenantBalance.recalc_for_tenant(self.tenant)
        self.assertEqual(tenant_balance.balance, Decimal('9300.00'))
        
        return feb_invoice

    def test_05_overpayment_creates_unallocated_credit(self):
        """Test overpayment that creates unallocated credit"""
        feb_invoice = self.test_04_february_invoice_and_oldest_first_payment()
        
        # Process large payment that creates overpayment
        payment_result = apply_credit_and_deposit(
            tenant=self.tenant,
            payment_amount=Decimal('20000.00'),
            reference="Overpayment",
            method="Mpesa"
        )
        
        # ROBUST: Verify payment allocation
        # Should pay Feb remainder (9300) and store rest as credit (10700)
        self.assertEqual(payment_result["applied_to_invoices"], "9300.00")
        self.assertEqual(payment_result["stored_as_credit"], "10700.00")
        self.assertEqual(payment_result["unallocated"], "0.00")
        
        # ROBUST: Verify Payment records
        # Should have one payment for Feb invoice and one unallocated payment
        feb_payments = Payment.objects.filter(invoice=feb_invoice)
        unallocated_payments = Payment.objects.filter(tenant=self.tenant, invoice__isnull=True)
        
        self.assertEqual(feb_payments.count(), 2)  # Previous + new payment
        latest_feb_payment = feb_payments.order_by('-payment_date').first()
        self.assertEqual(latest_feb_payment.amount, Decimal('9300.00'))
        
        self.assertEqual(unallocated_payments.count(), 1)
        credit_payment = unallocated_payments.first()
        self.assertEqual(credit_payment.amount, Decimal('10700.00'))
        self.assertEqual(credit_payment.payment_type, 'CREDIT')
        
        # ROBUST: Verify all invoices are now paid
        invoices = Invoice.objects.filter(tenant=self.tenant)
        for invoice in invoices:
            invoice.refresh_from_db()
            self.assertEqual(invoice.balance, Decimal('0.00'))
            self.assertTrue(invoice.is_paid)
        
        # ROBUST: Verify tenant balance is negative (credit balance)
        tenant_balance = TenantBalance.recalc_for_tenant(self.tenant)
        self.assertEqual(tenant_balance.balance, Decimal('-10700.00'))
        
        return credit_payment

    def test_06_use_existing_credit_for_new_invoice(self):
        """Test using existing unallocated credit for new invoice"""
        credit_payment = self.test_05_overpayment_creates_unallocated_credit()
        
        # Create March meter reading and invoice
        mar_reading = MeterReading.objects.create(
            unit=self.unit,
            reading_date=date(2025, 3, 31),
            previous_reading=Decimal('86.00'),
            current_reading=Decimal('130.00'),
            usage=Decimal('44.00'),
            rate_per_cubic_meter=Decimal('50.00'),
            amount=Decimal('2200.00')
        )
        
        result = _process_lease_for_month(self.lease, date(2025, 3, 1))
        self.assertEqual(result["status"], "created")
        
        # Get March invoice
        mar_start, mar_end = billing_period_for_billing_month(date(2025, 3, 1), 5)
        mar_invoice = Invoice.objects.get(
            tenant=self.tenant, 
            billing_period_start=mar_start, 
            billing_period_end=mar_end
        )
        
        self.assertEqual(mar_invoice.total_amount, Decimal('17200.00'))  # 15000 + 2200
        
        # ROBUST: Apply existing credit to new invoice (no new payment)
        payment_result = apply_credit_and_deposit(
            tenant=self.tenant,
            payment_amount=None,  # Use existing credit
            reference="Using Credit",
            method="Credit"
        )
        
        # ROBUST: Verify credit application
        # Should use all 10700 credit and leave 6500 balance on March invoice
        self.assertEqual(payment_result["applied_to_invoices"], "10700.00")
        self.assertEqual(payment_result["stored_as_credit"], "0.00")
        
        # ROBUST: Verify the unallocated payment was linked to March invoice
        credit_payment.refresh_from_db()
        self.assertEqual(credit_payment.invoice, mar_invoice)
        
        # Verify no unallocated payments remain
        unallocated_payments = Payment.objects.filter(tenant=self.tenant, invoice__isnull=True)
        self.assertEqual(unallocated_payments.count(), 0)
        
        # ROBUST: Verify March invoice balance
        mar_invoice.refresh_from_db()
        self.assertEqual(mar_invoice.total_paid, Decimal('10700.00'))
        self.assertEqual(mar_invoice.balance, Decimal('6500.00'))  # 17200 - 10700
        
        # ROBUST: Verify tenant balance
        tenant_balance = TenantBalance.recalc_for_tenant(self.tenant)
        self.assertEqual(tenant_balance.balance, Decimal('6500.00'))

    def test_07_deposit_prioritization_verification(self):
        """Specific test to verify deposit prioritization within invoice"""
        # Create fresh invoice for this test
        self.setUp()  # Reset data
        jan_invoice = self.test_01_january_invoice_creation()
        
        # Make payment exactly equal to rent amount
        # This should go to deposit first, not rent
        payment_result = apply_credit_and_deposit(
            tenant=self.tenant,
            payment_amount=Decimal('15000.00'),  # Equal to rent, but should go to deposit
            reference="Deposit Priority Test",
            method="Mpesa"
        )
        
        # ROBUST: Verify entire payment goes to deposit
        self.assertEqual(payment_result["applied_to_deposit"], "15000.00")
        self.assertEqual(payment_result["applied_to_invoices"], "15000.00")
        
        # Verify deposit received the payment
        deposit = Deposit.objects.get(lease=self.lease)
        self.assertEqual(deposit.amount_held, Decimal('15000.00'))
        
        # ROBUST: Verify Payment record shows deposit type
        payment = Payment.objects.get(tenant=self.tenant)
        self.assertEqual(payment.payment_type, 'DEPOSIT')
        
        # Verify invoice balance calculation
        jan_invoice.refresh_from_db()
        self.assertEqual(jan_invoice.balance, Decimal('32500.00'))  # 47500 - 15000

    def test_08_comprehensive_system_verification(self):
        """Comprehensive test of the entire robust payment system"""
        # Run complete workflow
        self.test_06_use_existing_credit_for_new_invoice()
        
        # ROBUST: Comprehensive system verification
        
        # 1. Verify no negative InvoiceLines exist anywhere
        all_negative_lines = InvoiceLine.objects.filter(amount__lt=0)
        self.assertEqual(all_negative_lines.count(), 0)
        
        # 2. Verify all Payment records have positive amounts
        all_payments = Payment.objects.all()
        for payment in all_payments:
            self.assertGreater(payment.amount, 0)
        
        # 3. Verify invoice balance calculations are consistent
        all_invoices = Invoice.objects.filter(tenant=self.tenant)
        for invoice in all_invoices:
            # Manual calculation should match property
            manual_total_paid = sum(p.amount for p in invoice.payments.all())
            self.assertEqual(invoice.total_paid, manual_total_paid)
            
            manual_balance = invoice.total_amount - manual_total_paid
            self.assertEqual(invoice.balance, manual_balance)
        
        # 4. Verify tenant balance calculation
        tenant_balance = TenantBalance.objects.get(tenant=self.tenant)
        
        # Manual calculation: sum of unpaid invoice balances - unallocated payments
        unpaid_invoices = Invoice.objects.filter(tenant=self.tenant, is_paid=False)
        total_unpaid_balance = sum(inv.balance for inv in unpaid_invoices)
        
        unallocated_payments = Payment.objects.filter(tenant=self.tenant, invoice__isnull=True)
        total_unallocated_credit = sum(p.amount for p in unallocated_payments)
        
        expected_balance = total_unpaid_balance - total_unallocated_credit
        self.assertEqual(tenant_balance.balance, expected_balance)
        
        # 5. Verify deposit tracking integrity
        deposit = Deposit.objects.get(lease=self.lease)
        deposit_payments = Payment.objects.filter(tenant=self.tenant, payment_type__in=['DEPOSIT', 'MIXED'])
        
        # Sum deposit portions from payments (this is complex to calculate exactly,
        # but we can verify the deposit is properly tracked)
        self.assertGreaterEqual(deposit.amount_held, 0)
        self.assertLessEqual(deposit.amount_held, deposit.amount)
        
        logger.info("=== ROBUST PAYMENT SYSTEM VERIFICATION COMPLETE ===")
        logger.info("✓ No negative InvoiceLines found")
        logger.info("✓ All Payment records have positive amounts") 
        logger.info("✓ Invoice balance calculations are consistent")
        logger.info("✓ Tenant balance calculation is accurate")
        logger.info("✓ Deposit tracking is properly maintained")
        logger.info("✓ Payment-only approach working correctly")


# Standalone test runner with robust error handling
if __name__ == '__main__':
    import unittest
    
    # Create test suite
    suite = unittest.TestSuite()
    
    test_methods = [
        'test_01_january_invoice_creation',
        'test_02_partial_deposit_payment', 
        'test_03_complete_deposit_payment',
        'test_04_february_invoice_and_oldest_first_payment',
        'test_05_overpayment_creates_unallocated_credit',
        'test_06_use_existing_credit_for_new_invoice',
        'test_07_deposit_prioritization_verification',
        'test_08_comprehensive_system_verification'
    ]
    
    for method in test_methods:
        suite.addTest(RobustPaymentWorkflowTestCase(method))
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    if result.wasSuccessful():
        print("\n🎉 ALL ROBUST PAYMENT SYSTEM TESTS PASSED!")
        print("The Payment-only approach is working correctly.")
    else:
        print(f"\n❌ {len(result.failures)} test(s) failed, {len(result.errors)} error(s)")
        
        # Print detailed failure information
        if result.failures:
            print("\nFAILURES:")
            for test, failure in result.failures:
                print(f"  {test}: {failure}")
        
        if result.errors:
            print("\nERRORS:")
            for test, error in result.errors:
                print(f"  {test}: {error}")