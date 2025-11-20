# test_payment_service.py
from decimal import Decimal
from django.test import TestCase
from apps.tenant_management.services.payment_service import PaymentService
from apps.tenant_management.models import Tenant, Property, Unit, Lease

class PaymentServiceTest(TestCase):
    def setUp(self):
        # Create test data
        self.property = Property.objects.create(
            name="Test Property",
            location="Test Location",
            water_policy="meter",
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
            start_date="2025-01-01",
            deposit_amount=Decimal('30000.00'),
            is_active=True
        )
    
    def test_payment_processing(self):
        """Test that payment processing works with the new service."""
        result = PaymentService.process_payment(
            tenant=self.tenant,
            amount=Decimal('10000.00'),
            reference="Test Payment",
            method="Mpesa"
        )
        
        self.assertIn("applied_to_invoices", result)
        self.assertIn("applied_to_deposit", result)
        self.assertIn("stored_as_credit", result)
        print("Payment service test passed")