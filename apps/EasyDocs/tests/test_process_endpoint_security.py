from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps.EasyDocs.models import (
    Client,
    ClientService,
    ClientServiceProcess,
    Process,
    Service,
    ServiceCategory,
)


class ProcessCompletionEndpointSecurityTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(
            first_name="Alice",
            last_name="Client",
            phone="0700000001",
        )
        self.service = Service.objects.create(
            name="Title Workflow",
            category=ServiceCategory.TITLE,
        )
        self.process = Process.objects.create(
            service=self.service,
            name="Verification",
            step_order=1,
            cost=100,
            message="Your process is in progress",
        )
        self.client_service = ClientService.objects.create(
            client=self.client_obj,
            service=self.service,
            land_description="Plot 1",
        )
        self.step = ClientServiceProcess.objects.create(
            client_service=self.client_service,
            process=self.process,
            status="in_progress",
        )
        self.url = reverse("mark_process_completed", args=[self.step.id])

    def test_requires_login(self):
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        self.step.refresh_from_db()
        self.assertEqual(self.step.status, "in_progress")

    def test_denies_user_without_permission_or_assignment(self):
        user = User.objects.create_user(username="staff1", password="pass123")
        self.client.force_login(user)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        self.step.refresh_from_db()
        self.assertEqual(self.step.status, "in_progress")

    def test_allows_assigned_accepted_user(self):
        user = User.objects.create_user(username="assignee", password="pass123")
        self.client_service.assigned_employee = user
        self.client_service.assignment_status = "accepted"
        self.client_service.save(update_fields=["assigned_employee", "assignment_status"])

        self.client.force_login(user)
        response = self.client.post(self.url, HTTP_REFERER="/clients/")

        self.assertEqual(response.status_code, 302)
        self.step.refresh_from_db()
        self.assertEqual(self.step.status, "completed")
