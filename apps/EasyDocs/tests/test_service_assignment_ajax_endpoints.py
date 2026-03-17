from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps.EasyDocs.models import Client, ClientService, Process, Service, ServiceAssignmentLog, ServiceCategory


class ServiceAssignmentAjaxEndpointTests(TestCase):
    def setUp(self):
        self.assigner = User.objects.create_user(username="assigner_ajax", password="pass")
        self.assignee = User.objects.create_user(username="assignee_ajax", password="pass")

        self.client_obj = Client.objects.create(
            first_name="Ajax",
            last_name="Client",
            phone="0700000600",
        )
        self.service = Service.objects.create(
            name="Ajax Service",
            category=ServiceCategory.TITLE,
        )
        Process.objects.create(
            service=self.service,
            name="Ajax Step",
            step_order=1,
            cost=50,
            message="Ajax step",
            notification_enabled=False,
        )
        self.client_service = ClientService.objects.create(
            client=self.client_obj,
            service=self.service,
            land_description="Ajax Plot",
            assigned_employee=self.assignee,
            assignment_status="pending_acceptance",
        )
        ServiceAssignmentLog.objects.create(
            client_service=self.client_service,
            assigned_employee=self.assignee,
            action="assigned",
            assigned_by=self.assigner,
            reason="Assigned for ajax endpoint test",
        )

    @patch("apps.EasyDocs.services.assignments.send_push_to_user")
    def test_accept_service_assignment_returns_json_success(self, mock_send_push):
        self.client.force_login(self.assignee)

        response = self.client.post(
            reverse("accept_service_assignment", args=[self.client_service.id]),
            data={"reason": "accept from dashboard"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["client_service_id"], self.client_service.id)
        self.assertEqual(payload["assignment_status"], "accepted")

        self.client_service.refresh_from_db()
        self.assertEqual(self.client_service.assignment_status, "accepted")
        mock_send_push.assert_called()

    @patch("apps.EasyDocs.services.assignments.send_push_to_user")
    def test_decline_service_assignment_returns_json_success(self, mock_send_push):
        self.client.force_login(self.assignee)
        self.client_service.assignment_status = "accepted"
        self.client_service.save(update_fields=["assignment_status"])

        response = self.client.post(
            reverse("decline_service_assignment", args=[self.client_service.id]),
            data={"reason": "decline from dashboard"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["client_service_id"], self.client_service.id)
        self.assertEqual(payload["assignment_status"], "unassigned")

        self.client_service.refresh_from_db()
        self.assertEqual(self.client_service.assignment_status, "unassigned")
        self.assertIsNone(self.client_service.assigned_employee)
        mock_send_push.assert_called()
