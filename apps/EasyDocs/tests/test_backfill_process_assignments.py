from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.EasyDocs.models import (
    Client,
    ClientService,
    ClientServiceProcess,
    ClientServiceProcessAssignment,
    Process,
    Service,
    ServiceCategory,
)


class BackfillProcessAssignmentsCommandTests(TestCase):
    def setUp(self):
        self.assignee = User.objects.create_user(username="worker1", password="pass123")

        self.client_obj = Client.objects.create(
            first_name="Backfill",
            last_name="Client",
            phone="0700000200",
        )
        self.service = Service.objects.create(
            name="Title Service",
            category=ServiceCategory.TITLE,
        )
        self.process = Process.objects.create(
            service=self.service,
            name="Initial Review",
            step_order=1,
            cost=100,
            message="Review started",
            notification_enabled=False,
        )
        self.client_service = ClientService.objects.create(
            client=self.client_obj,
            service=self.service,
            land_description="Plot 200",
            assigned_employee=self.assignee,
            assignment_status="accepted",
        )
        self.step = ClientServiceProcess.objects.get(
            client_service=self.client_service,
            process=self.process,
        )

    def test_command_creates_assignment_from_service_level_assignee(self):
        call_command("backfill_process_assignments")

        assignment = ClientServiceProcessAssignment.objects.get(
            client_service_process=self.step,
            assignee=self.assignee,
            is_active=True,
        )
        self.assertEqual(assignment.acceptance_status, "accepted")
        self.assertEqual(assignment.completion_status, "pending")

    def test_command_is_idempotent(self):
        call_command("backfill_process_assignments")
        call_command("backfill_process_assignments")

        count = ClientServiceProcessAssignment.objects.filter(
            client_service_process=self.step,
            assignee=self.assignee,
            is_active=True,
        ).count()
        self.assertEqual(count, 1)

    def test_dry_run_does_not_write(self):
        call_command("backfill_process_assignments", "--dry-run")

        self.assertFalse(
            ClientServiceProcessAssignment.objects.filter(
                client_service_process=self.step,
                assignee=self.assignee,
            ).exists()
        )

    def test_include_completed_backfills_completed_step(self):
        self.step.status = "completed"
        self.step.completed_at = timezone.now()
        self.step.save(update_fields=["status", "completed_at"])

        call_command("backfill_process_assignments")
        self.assertFalse(
            ClientServiceProcessAssignment.objects.filter(
                client_service_process=self.step,
                assignee=self.assignee,
            ).exists()
        )

        call_command("backfill_process_assignments", "--include-completed")
        assignment = ClientServiceProcessAssignment.objects.get(
            client_service_process=self.step,
            assignee=self.assignee,
            is_active=True,
        )
        self.assertEqual(assignment.completion_status, "completed")
        self.assertIsNotNone(assignment.completed_at)
