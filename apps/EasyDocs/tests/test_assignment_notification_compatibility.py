from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from apps.EasyDocs.models import (
    Client,
    ClientService,
    Process,
    Service,
    ServiceAssignmentLog,
    ServiceCategory,
)
from apps.EasyDocs.services.assignments import (
    handle_accept_service,
    handle_decline_service,
)
from apps.notifications.models import Notification


class AssignmentNotificationCompatibilityTests(TestCase):
    def setUp(self):
        self.assigner = User.objects.create_user(username="notify_assigner", password="pass")
        self.assignee = User.objects.create_user(username="notify_assignee", password="pass")

        self.client_obj = Client.objects.create(
            first_name="Notify",
            last_name="Compat",
            phone="0700000500",
        )
        self.service = Service.objects.create(
            name="Notification Service",            category=ServiceCategory.TITLE,
        )
        Process.objects.create(
            service=self.service,
            name="Notify Step",
            step_order=1,
            cost=20,
            message="Notify step",
            notification_enabled=False,
        )

        self.client_service = ClientService.objects.create(
            client=self.client_obj,
            service=self.service,
            land_description="Plot Notify",
            assigned_employee=self.assignee,
            assignment_status="pending_acceptance",
        )

        ServiceAssignmentLog.objects.create(
            client_service=self.client_service,
            assigned_employee=self.assignee,
            action="assigned",
            assigned_by=self.assigner,
            reason="Initial assignment",
        )

    @patch("apps.EasyDocs.services.assignments.send_push_to_user")
    def test_accept_still_creates_notification_for_assigner(self, mock_send_push):
        result = handle_accept_service(self.client_service.id, self.assignee, reason="accepting")

        self.assertTrue(result["success"])
        self.assertTrue(
            Notification.objects.filter(
                user=self.assigner,
                title="Service Accepted",
            ).exists()
        )
        mock_send_push.assert_called()

    @patch("apps.EasyDocs.services.assignments.send_push_to_user")
    def test_decline_still_creates_notification_for_assigner(self, mock_send_push):
        # Allow decline from accepted state too
        self.client_service.assignment_status = "accepted"
        self.client_service.save(update_fields=["assignment_status"])

        result = handle_decline_service(self.client_service.id, self.assignee, reason="cannot take")

        self.assertTrue(result["success"])
        self.client_service.refresh_from_db()
        self.assertIsNone(self.client_service.assigned_employee)
        self.assertEqual(self.client_service.assignment_status, "unassigned")

        self.assertTrue(
            Notification.objects.filter(
                user=self.assigner,
                title="Service Declined",
            ).exists()
        )
        mock_send_push.assert_called()
