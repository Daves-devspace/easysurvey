import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client as DjangoClient
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.EasyDocs.models import Booking, Client, ClientService, Service, ServiceCategory


class BookingDuplicateGuardTests(TestCase):
    def setUp(self):
        self.http = DjangoClient(enforce_csrf_checks=False)
        self.user = get_user_model().objects.create_user(
            username="booking_guard_user",
            password="pass",
            is_staff=True,
        )
        self.http.force_login(self.user)

        self.service = Service.objects.create(
            name="Survey Service",
            category=ServiceCategory.GROUND,
            total_price=100,
        )
        self.client_obj = Client.objects.create(
            first_name="Booking",
            last_name="Client",
            phone="0700009999",
        )
        self.client_service = ClientService.objects.create(
            client=self.client_obj,
            service=self.service,
            land_description="Plot A",
            assigned_employee=self.user,
            assignment_status="accepted",
        )

    def _datetime_local(self, dt):
        return timezone.localtime(dt).replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")

    def _ajax_post(self, url, payload):
        return self.http.post(
            url,
            data=payload,
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

    def test_create_rejects_duplicate_client_service_and_datetime(self):
        scheduled = timezone.now().replace(second=0, microsecond=0) + timedelta(days=1)
        Booking.objects.create(client_service=self.client_service, scheduled_date=scheduled)

        url = reverse("booking_create", kwargs={"client_service_id": self.client_service.pk})
        resp = self._ajax_post(
            url,
            {
                "scheduled_date": self._datetime_local(scheduled),
                "dispatch_message": "duplicate attempt",
            },
        )

        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.content)
        self.assertFalse(data["success"])
        self.assertIn("scheduled_date", data["errors"])
        self.assertEqual(
            Booking.objects.filter(
                client_service=self.client_service,
                scheduled_date=scheduled,
            ).count(),
            1,
        )

    def test_update_rejects_collision_with_existing_booking_datetime(self):
        base = timezone.now().replace(second=0, microsecond=0)
        dt_a = base + timedelta(days=2)
        dt_b = base + timedelta(days=3)

        existing = Booking.objects.create(
            client_service=self.client_service,
            scheduled_date=dt_a,
        )
        to_edit = Booking.objects.create(
            client_service=self.client_service,
            scheduled_date=dt_b,
            dispatch_message="before",
        )

        url = reverse("edit_booking", kwargs={"pk": to_edit.pk})
        resp = self._ajax_post(
            url,
            {
                "scheduled_date": self._datetime_local(existing.scheduled_date),
                "dispatch_message": "attempt collision",
            },
        )

        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.content)
        self.assertFalse(data["success"])
        self.assertIn("scheduled_date", data["errors"])

        to_edit.refresh_from_db()
        self.assertEqual(
            to_edit.scheduled_date.replace(second=0, microsecond=0),
            dt_b.replace(second=0, microsecond=0),
        )
