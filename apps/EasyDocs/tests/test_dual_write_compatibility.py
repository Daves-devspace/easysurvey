from django.contrib.auth.models import User
from django.test import TestCase

from apps.EasyDocs.models import (
    Client,
    ClientService,
    ClientServiceProcessAssignment,
    ClientServiceProcessAssignmentLog,
    Process,
    Service,
    ServiceCategory,
)
from apps.EasyDocs.services.process_assignments import (
    mark_process_assignments_accepted,
    mark_process_assignments_declined,
    sync_service_assignment_to_process_assignments,
)


class ProcessAssignmentDualWriteCompatibilityTests(TestCase):
    def setUp(self):
        self.assigner = User.objects.create_user(username="assigner_dw", password="pass123")
        self.assignee_a = User.objects.create_user(username="worker_a", password="pass123")
        self.assignee_b = User.objects.create_user(username="worker_b", password="pass123")

        self.client_obj = Client.objects.create(
            first_name="Dual",
            last_name="Write",
            phone="0700000300",
        )
        self.service = Service.objects.create(
            name="Title Approval",
            category=ServiceCategory.TITLE,
        )
        Process.objects.create(
            service=self.service,
            name="Step 1",
            step_order=1,
            cost=50,
            message="Step 1",
            notification_enabled=False,
        )
        Process.objects.create(
            service=self.service,
            name="Step 2",
            step_order=2,
            cost=60,
            message="Step 2",
            notification_enabled=False,
        )

        self.client_service = ClientService.objects.create(
            client=self.client_obj,
            service=self.service,
            land_description="Plot 300",
            assigned_employee=self.assignee_a,
            assignment_status="pending_acceptance",
        )

    def test_sync_creates_pending_assignments_for_open_steps(self):
        result = sync_service_assignment_to_process_assignments(
            client_service=self.client_service,
            assigned_employee=self.assignee_a,
            assigned_by=self.assigner,
            reason="initial assign",
        )

        self.assertEqual(result["created"], 2)
        assignments = ClientServiceProcessAssignment.objects.filter(
            client_service_process__client_service=self.client_service,
            assignee=self.assignee_a,
            is_active=True,
        )
        self.assertEqual(assignments.count(), 2)
        self.assertTrue(all(a.acceptance_status == "pending" for a in assignments))

        logs = ClientServiceProcessAssignmentLog.objects.filter(action="assigned")
        self.assertEqual(logs.count(), 2)

    def test_sync_deactivates_previous_assignee_on_reassignment(self):
        sync_service_assignment_to_process_assignments(
            client_service=self.client_service,
            assigned_employee=self.assignee_a,
            assigned_by=self.assigner,
            reason="initial",
        )

        self.client_service.assigned_employee = self.assignee_b
        self.client_service.assignment_status = "reassigned"
        self.client_service.save(update_fields=["assigned_employee", "assignment_status"])

        sync_service_assignment_to_process_assignments(
            client_service=self.client_service,
            assigned_employee=self.assignee_b,
            assigned_by=self.assigner,
            reason="reassigned",
        )

        active_a = ClientServiceProcessAssignment.objects.filter(
            client_service_process__client_service=self.client_service,
            assignee=self.assignee_a,
            is_active=True,
        ).count()
        active_b = ClientServiceProcessAssignment.objects.filter(
            client_service_process__client_service=self.client_service,
            assignee=self.assignee_b,
            is_active=True,
        ).count()

        self.assertEqual(active_a, 0)
        self.assertEqual(active_b, 2)

    def test_mark_accepted_updates_all_active_rows_for_user(self):
        sync_service_assignment_to_process_assignments(
            client_service=self.client_service,
            assigned_employee=self.assignee_a,
            assigned_by=self.assigner,
        )

        updated = mark_process_assignments_accepted(self.client_service, self.assignee_a, reason="accepted")
        self.assertEqual(updated, 2)

        assignments = ClientServiceProcessAssignment.objects.filter(
            client_service_process__client_service=self.client_service,
            assignee=self.assignee_a,
            is_active=True,
        )
        self.assertTrue(all(a.acceptance_status == "accepted" for a in assignments))
        self.assertTrue(all(a.accepted_at is not None for a in assignments))

    def test_mark_declined_marks_inactive(self):
        sync_service_assignment_to_process_assignments(
            client_service=self.client_service,
            assigned_employee=self.assignee_a,
            assigned_by=self.assigner,
        )

        updated = mark_process_assignments_declined(self.client_service, self.assignee_a, reason="declined")
        self.assertEqual(updated, 2)

        assignments = ClientServiceProcessAssignment.objects.filter(
            client_service_process__client_service=self.client_service,
            assignee=self.assignee_a,
        )
        self.assertTrue(all(a.acceptance_status == "declined" for a in assignments))
        self.assertTrue(all(a.is_active is False for a in assignments))
