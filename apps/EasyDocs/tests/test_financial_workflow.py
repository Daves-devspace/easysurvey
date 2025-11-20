# tests/test_financial_workflow.py

from decimal import Decimal
from datetime import date, timedelta
from django.test import TestCase, Client as DjangoClient
from django.urls import reverse
from django.utils import timezone

from apps.EasyDocs.models import (
    Client, Service, ClientService, ClientSubService, ServiceCategory, Payment, Expense
)
from apps.EasyDocs.services.services import create_client_service_with_overrides

class FinancialWorkflowTestCase(TestCase):

    def setUp(self):
        # Create sample client
        self.client_obj = Client.objects.create(
            first_name="John", last_name="Doe", phone="0712345678"
        )

        # Create sample services
        self.title_service = Service.objects.create(
            name="Title Registration",
            category=ServiceCategory.TITLE,
            total_price=Decimal('1000.00'),
            full_total_price=Decimal('1500.00')
        )
        self.ground_service = Service.objects.create(
            name="Ground Booking",
            category=ServiceCategory.GROUND,
            total_price=Decimal('500.00'),
            full_total_price=Decimal('500.00')
        )

        # Create client service and payment
        self.client_service = ClientService.objects.create(
            client=self.client_obj,
            service=self.title_service,
            land_description="Plot 123"
        )
        self.payment = Payment.objects.create(
            client_service=self.client_service,
            amount=Decimal('500.00'),
            payment_date=timezone.now(),
            institution_cost_snapshot=Decimal('100.00'),
            overridden_total_snapshot=Decimal('500.00')
        )

        self.client_api = DjangoClient()

    def test_service_payment_split(self):
        """Test proportional split of payments between institution and company revenue."""
        from apps.EasyDocs.clients.client_views import get_revenue_from_payments

        gross, company, institution = get_revenue_from_payments(date.today().year)
        self.assertEqual(gross, Decimal('500.00'))
        self.assertEqual(company, Decimal('400.00'))  # 500 - 100
        self.assertEqual(institution, Decimal('100.00'))

    def test_add_client_service_view(self):
        """Test adding a client service via ClientServiceManageView."""
        url = reverse('client_service_manage', kwargs={'client_id': self.client_obj.id})
        post_data = {
            'service': self.ground_service.id,
            'land_description': 'New Plot',
            'scheduled_date': timezone.now().date()
        }
        response = self.client_api.post(url, data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ClientService.objects.filter(client=self.client_obj, service=self.ground_service).exists())

    def test_edit_client_service_view(self):
        """Test editing an existing client service."""
        url = reverse('client_service_manage', kwargs={'client_id': self.client_obj.id})
        post_data = {
            'client_service_id': self.client_service.id,
            'service': self.ground_service.id,
            'land_description': 'Updated Plot'
        }
        response = self.client_api.post(url, data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.client_service.refresh_from_db()
        self.assertEqual(self.client_service.land_description, 'Updated Plot')
        self.assertEqual(self.client_service.service, self.ground_service)

    def test_delete_client_service_view(self):
        """Test deleting a client service."""
        url = reverse('delete_client_service', kwargs={'client_id': self.client_obj.id})
        post_data = {'client_service_id': self.client_service.id}
        response = self.client_api.post(url, data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ClientService.objects.filter(id=self.client_service.id).exists())

    def test_add_client_subservice_view(self):
        """Test adding a client subservice."""
        from apps.EasyDocs.forms import ClientSubServiceForm
        subservice_data = {
            'client_service': self.client_service.id,
            'sub_service': self.title_service.id,
            'overridden_price': Decimal('2000.00')
        }
        form = ClientSubServiceForm(data=subservice_data)
        self.assertTrue(form.is_valid())
        subservice = form.save(commit=False)
        subservice.client_service = self.client_service
        subservice.save()
        self.assertEqual(ClientSubService.objects.count(), 1)
        self.assertEqual(subservice.overridden_price, Decimal('2000.00'))

    def test_get_available_years(self):
        """Test available years returned by payments."""
        from apps.EasyDocs.clients.client_views import get_available_years
        years = get_available_years()
        self.assertIn(date.today().year, years)

    def test_monthly_company_revenue_zero_filled(self):
        """Test that months without payments return zero."""
        from apps.EasyDocs.clients.client_views import monthly_company_revenue
        revenue = monthly_company_revenue(date.today().year)
        self.assertEqual(len(revenue), 12)
        # Only one payment in current month
        self.assertEqual(sum(revenue), Decimal('400.00'))

    def test_service_snapshot_edge_case_zero_division(self):
        """Ensure no division by zero if overridden snapshot is zero."""
        Payment.objects.create(
            client_service=self.client_service,
            amount=Decimal('300.00'),
            payment_date=timezone.now(),
            institution_cost_snapshot=Decimal('50.00'),
            overridden_total_snapshot=Decimal('0.00')  # edge case
        )
        from apps.EasyDocs.clients.client_views import get_revenue_from_payments
        gross, company, institution = get_revenue_from_payments(date.today().year)
        self.assertEqual(company + institution, gross)  # total still equals gross collected
