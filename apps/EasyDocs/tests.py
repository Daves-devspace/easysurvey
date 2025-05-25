from django.test import TestCase

# Create your tests here.
from freezegun import freeze_time
from datetime import timedelta
from django.utils import timezone
from django.test import TestCase
from unittest.mock import patch

from apps.EasyDocs.models import Booking, ClientService, MessageLog, Client, Service
from apps.EasyDocs.tasks import send_today_ground_reminders

class SendGroundRemindersTest(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(first_name="Test", phone="+254700000000")
        self.service = Service.objects.create(name="Test Survey")
        self.client_service = ClientService.objects.create(client=self.client_obj, service=self.service)
        self.tomorrow = timezone.now() + timedelta(days=1)
        self.booking = Booking.objects.create(
            client_service=self.client_service,
            scheduled_date=self.tomorrow
        )

    @patch("apps.EasyDocs.utils.send_single_sms")  # 👈 This is the right place to patch
    def test_send_today_ground_reminders(self, mock_send_single_sms):
        mock_send_single_sms.return_value = (True, {"message_id": "123"})

        with freeze_time(self.tomorrow):
            send_today_ground_reminders()

        logs = MessageLog.objects.filter(client=self.client_obj, reason="Ground Service Reminder")
        self.assertEqual(logs.count(), 1)
        self.assertIn("Reminder:", logs.first().message)