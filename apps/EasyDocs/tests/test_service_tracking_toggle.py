from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.EasyDocs.models import Client, ClientService, Service, ServiceCategory, SiteSettings
from apps.EasyDocs.services.feature_flags import is_service_tracking_enabled


class ServiceTrackingToggleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="settings_toggle_user", password="pass")
        self.client.force_login(self.user)

        self.client_obj = Client.objects.create(
            first_name="Tracking",
            last_name="Client",
            phone="0700012345",
        )
        self.service = Service.objects.create(
            name="Tracking Service",
            category=ServiceCategory.OTHERS,
            total_price=1200,
            expected_duration_days=14,
        )

    def _post_url(self):
        return reverse("client-service", kwargs={"client_id": self.client_obj.id})

    def _settings_url(self):
        return reverse("update_site_settings")

    @staticmethod
    def _set_tracking(enabled: bool):
        settings, _ = SiteSettings.objects.get_or_create(singleton_enforcer=True)
        settings.allow_service_tracking = enabled
        settings.save(update_fields=["allow_service_tracking"])

    def test_service_tracking_flag_defaults_true(self):
        self.assertTrue(is_service_tracking_enabled())

    def test_add_service_clears_duration_and_deadlines_when_tracking_off(self):
        self._set_tracking(False)

        response = self.client.post(
            self._post_url(),
            {
                "add_client_service": "1",
                "client": str(self.client_obj.id),
                "category": ServiceCategory.OTHERS,
                "service": str(self.service.id),
                "land_description": "Tracking disabled add",
                "expected_duration_days": "21",
            },
        )

        self.assertEqual(response.status_code, 302)
        cs = ClientService.objects.filter(
            client=self.client_obj,
            service=self.service,
        ).latest("id")

        self.assertIsNone(cs.expected_duration_days)
        self.assertIsNone(cs.deadline)
        self.assertIsNone(cs.original_deadline)

    def test_edit_service_clears_existing_duration_and_deadlines_when_tracking_off(self):
        now = timezone.now()
        cs = ClientService.objects.create(
            client=self.client_obj,
            service=self.service,
            land_description="Before edit",
            expected_duration_days=10,
            deadline=now + timedelta(days=10),
            original_deadline=now + timedelta(days=10),
        )
        self._set_tracking(False)

        response = self.client.post(
            self._post_url(),
            {
                "client_service_id": str(cs.id),
                "client": str(self.client_obj.id),
                "category": ServiceCategory.OTHERS,
                "service": str(self.service.id),
                "land_description": "After edit",
                "expected_duration_days": "30",
            },
        )

        self.assertEqual(response.status_code, 302)
        cs.refresh_from_db()
        self.assertIsNone(cs.expected_duration_days)
        self.assertIsNone(cs.deadline)
        self.assertIsNone(cs.original_deadline)

    def test_switching_tracking_off_in_settings_clears_existing_service_tracking_fields(self):
        now = timezone.now()
        cs = ClientService.objects.create(
            client=self.client_obj,
            service=self.service,
            land_description="Before settings toggle",
            expected_duration_days=7,
            deadline=now + timedelta(days=7),
            original_deadline=now + timedelta(days=7),
        )

        settings, _ = SiteSettings.objects.get_or_create(singleton_enforcer=True)
        settings.company_name = "Plotsync"
        settings.allow_service_tracking = True
        settings.save()

        response = self.client.post(
            self._settings_url(),
            {
                "company_name": "Plotsync",
                # Intentionally omitted: allow_service_tracking (unchecked -> False)
            },
        )

        self.assertEqual(response.status_code, 302)
        settings.refresh_from_db()
        self.assertFalse(settings.allow_service_tracking)

        cs.refresh_from_db()
        self.assertIsNone(cs.expected_duration_days)
        self.assertIsNone(cs.deadline)
        self.assertIsNone(cs.original_deadline)
