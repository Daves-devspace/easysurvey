from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.EasyDocs.models import (
    Client,
    ClientService,
    ClientServiceProcess,
    ClientServiceProcessAssignment,
    ClientServiceProcessAssignmentLog,
    Process,
    Service,
    ServiceCategory,
)


class ProcessAssignmentModelTests(TestCase):
    def setUp(self):
        self.assignee = User.objects.create_user(username="assignee_1", password="pass123")
        self.assigner = User.objects.create_user(username="assigner_1", password="pass123")

        self.client_obj = Client.objects.create(
            first_name="John",
            last_name="Doe",
            phone="0700000010",
        )
        self.service = Service.objects.create(
            name="Title Transfer",
            category=ServiceCategory.TITLE,
        )
        self.process = Process.objects.create(
            service=self.service,
            name="Review Documents",
            step_order=1,
            cost=100,
            message="Process update",
            notification_enabled=False,
        )
        self.client_service = ClientService.objects.create(
            client=self.client_obj,
            service=self.service,
            land_description="Plot 10",
        )
        self.step = ClientServiceProcess.objects.get(
            client_service=self.client_service,
            process=self.process,
        )

    def test_assignment_defaults(self):
        assignment = ClientServiceProcessAssignment.objects.create(
            client_service_process=self.step,
            assignee=self.assignee,
            assigned_by=self.assigner,
        )

        self.assertTrue(assignment.is_active)
        self.assertEqual(assignment.acceptance_status, "pending")
        self.assertEqual(assignment.completion_status, "pending")

    def test_unique_active_assignment_per_assignee_per_step(self):
        ClientServiceProcessAssignment.objects.create(
            client_service_process=self.step,
            assignee=self.assignee,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ClientServiceProcessAssignment.objects.create(
                    client_service_process=self.step,
                    assignee=self.assignee,
                )

    def test_reassignment_allowed_when_previous_row_inactive(self):
        original = ClientServiceProcessAssignment.objects.create(
            client_service_process=self.step,
            assignee=self.assignee,
        )
        original.is_active = False
        original.save(update_fields=["is_active"])

        replacement = ClientServiceProcessAssignment.objects.create(
            client_service_process=self.step,
            assignee=self.assignee,
            assigned_by=self.assigner,
        )

        self.assertNotEqual(original.id, replacement.id)
        self.assertTrue(replacement.is_active)

    def test_assignment_log_creation(self):
        assignment = ClientServiceProcessAssignment.objects.create(
            client_service_process=self.step,
            assignee=self.assignee,
            assigned_by=self.assigner,
        )

        log = ClientServiceProcessAssignmentLog.objects.create(
            assignment=assignment,
            action="assigned",
            acted_by=self.assigner,
            reason="Initial assignment",
        )

        self.assertEqual(log.assignment_id, assignment.id)
        self.assertEqual(log.action, "assigned")
        self.assertEqual(log.meta, {})
