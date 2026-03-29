from django.test import TestCase

# Create your tests here.
from freezegun import freeze_time
from datetime import timedelta
from django.utils import timezone
from django.test import TestCase
from unittest.mock import patch

from apps.EasyDocs.models import Booking, ClientService, MessageLog, Client, Service
from apps.EasyDocs.tasks import send_today_ground_reminders

# class SendGroundRemindersTest(TestCase):
#     def setUp(self):
#         self.client_obj = Client.objects.create(first_name="Test", phone="+254700000000")
#         self.service = Service.objects.create(name="Test Survey")
#         self.client_service = ClientService.objects.create(client=self.client_obj, service=self.service)
#         self.tomorrow = timezone.now() + timedelta(days=1)
#         self.booking = Booking.objects.create(
#             client_service=self.client_service,
#             scheduled_date=self.tomorrow
#         )

#     @patch("apps.EasyDocs.utils.send_single_sms")  # 👈 This is the right place to patch
#     def test_send_today_ground_reminders(self, mock_send_single_sms):
#         mock_send_single_sms.return_value = (True, {"message_id": "123"})

#         with freeze_time(self.tomorrow):
#             send_today_ground_reminders()

#         logs = MessageLog.objects.filter(client=self.client_obj, reason="Ground Service Reminder")
#         self.assertEqual(logs.count(), 1)
#         self.assertIn("Reminder:", logs.first().message)
        
        
        
from django.test import TestCase
from decimal import Decimal
from datetime import datetime
from django.utils import timezone

from apps.EasyDocs.analytics import get_revenue_from_payments
from apps.EasyDocs.models import Service, ServiceCategory, ClientService, Payment, ClientSubService, PaymentHistory

class RevenueComputationTest(TestCase):
    def setUp(self):
        tz = timezone.get_current_timezone()
        # TODO: create Service (adjust fields to your model)
        self.service = Service.objects.create(name="Land transfer", category=ServiceCategory.TITLE, total_price=Decimal('2000.00'), full_total_price=Decimal('2000.00'))

        # TODO: create ClientService (adjust fields)
        self.client_service = ClientService.objects.create(service=self.service, requested_at=timezone.make_aware(datetime(2025,10,28,12,0,0), tz))

        # create payments (three main + one subservice)
        Payment.objects.create(client_service=self.client_service, amount=Decimal('500.00'), payment_date=timezone.make_aware(datetime(2025,10,28,15,25,0), tz), institution_cost_snapshot=Decimal('1000.00'), overridden_total_snapshot=Decimal('2000.00'))
        Payment.objects.create(client_service=self.client_service, amount=Decimal('500.00'), payment_date=timezone.make_aware(datetime(2025,10,28,15,25,0), tz), institution_cost_snapshot=Decimal('1000.00'), overridden_total_snapshot=Decimal('2000.00'))
        Payment.objects.create(client_service=self.client_service, amount=Decimal('1000.00'), payment_date=timezone.make_aware(datetime(2025,10,30,16,48,0), tz), institution_cost_snapshot=Decimal('1000.00'), overridden_total_snapshot=Decimal('3000.00'))

        # create client subservice that corresponds to the last payment (paid_amount=1000)
        ClientSubService.objects.create(client_service=self.client_service, sub_service_id=1, paid_amount=Decimal('1000.00'), added_on=timezone.make_aware(datetime(2025,10,30,13,48,43), tz), overridden_price=Decimal('1000.00'))

        # If your application uses PaymentHistory to mark subservice payments, create it accordingly
        # PaymentHistory.objects.create(payment=..., reason='sub_service', sub_service=..., ...)

    def test_revenue_totals(self):
        today = timezone.localdate()  # or use date(2025,10,30)
        res = get_revenue_from_payments(2025, up_to_date=today)
        self.assertEqual(res['gross_total'], Decimal('3000.00'))
        self.assertEqual(res['company_total'], Decimal('1000.00'))
        self.assertEqual(res['inst_total'], Decimal('2000.00'))


