from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from apps.EasyDocs.models import (
    Client,
    ClientService,
    ClientServiceProcess,
    ClientServiceProcessAssignment,
    Process,
    Service,
    ServiceCategory,
)


class ReconcileProcessAssignmentsCommandTests(TestCase):
    def setUp(self):
        self.assigner = User.objects.create_user(username="reconcile_assigner", password="pass")
        self.worker_a = User.objects.create_user(username="reconcile_worker_a", password="pass")
        self.worker_b = User.objects.create_user(username="reconcile_worker_b", password="pass")

        self.client_obj = Client.objects.create(
            first_name="Recon",
            last_name="Client",
            phone="0700000400",
        )
        self.service = Service.objects.create(
            name="Recon Service",
            category=ServiceCategory.TITLE,
        )
        self.process_1 = Process.objects.create(
            service=self.service,
            name="Recon Step 1",
            step_order=1,
            cost=30,
            message="Step 1",
            notification_enabled=False,
        )
        self.process_2 = Process.objects.create(
            service=self.service,
            name="Recon Step 2",
            step_order=2,
            cost=40,
            message="Step 2",
            notification_enabled=False,
        )

        self.client_service = ClientService.objects.create(
            client=self.client_obj,
            service=self.service,
            land_description="Plot Recon",
            assigned_employee=self.worker_a,
            assignment_status="accepted",
        )

        self.step_1 = ClientServiceProcess.objects.get(
            client_service=self.client_service,
            process=self.process_1,
        )
        self.step_2 = ClientServiceProcess.objects.get(
            client_service=self.client_service,
            process=self.process_2,
        )

    def test_dry_run_reports_drift_without_writing(self):
        # stale assignment to wrong employee
        ClientServiceProcessAssignment.objects.create(
            client_service_process=self.step_1,
            assignee=self.worker_b,
            assigned_by=self.assigner,
            is_active=True,
            acceptance_status="pending",
        )

        out = StringIO()
        call_command("reconcile_process_assignments", "--dry-run", stdout=out)
        output = out.getvalue()

        self.assertIn("[DRY RUN]", output)
        # no writes in dry run
        active_ids = set(
            ClientServiceProcessAssignment.objects.filter(
                client_service_process=self.step_1,
                is_active=True,
            ).values_list("assignee_id", flat=True)
        )
        self.assertEqual(active_ids, {self.worker_b.id})

    def test_apply_replaces_stale_with_service_assignee(self):
        ClientServiceProcessAssignment.objects.create(
            client_service_process=self.step_1,
            assignee=self.worker_b,
            assigned_by=self.assigner,
            is_active=True,
            acceptance_status="pending",
        )

        call_command("reconcile_process_assignments")

        active_ids = set(
            ClientServiceProcessAssignment.objects.filter(
                client_service_process=self.step_1,
                is_active=True,
            ).values_list("assignee_id", flat=True)
        )
        self.assertEqual(active_ids, {self.worker_a.id})

    def test_apply_deactivates_rows_when_service_unassigned(self):
        ClientServiceProcessAssignment.objects.create(
            client_service_process=self.step_1,
            assignee=self.worker_a,
            assigned_by=self.assigner,
            is_active=True,
            acceptance_status="accepted",
        )
        self.client_service.assigned_employee = None
        self.client_service.assignment_status = "unassigned"
        self.client_service.save(update_fields=["assigned_employee", "assignment_status"])

        call_command("reconcile_process_assignments")

        self.assertFalse(
            ClientServiceProcessAssignment.objects.filter(
                client_service_process=self.step_1,
                is_active=True,
            ).exists()
        )

    def test_apply_reconciles_acceptance_status_from_service(self):
        ClientServiceProcessAssignment.objects.create(
            client_service_process=self.step_1,
            assignee=self.worker_a,
            assigned_by=self.assigner,
            is_active=True,
            acceptance_status="pending",
        )
        self.client_service.assignment_status = "accepted"
        self.client_service.save(update_fields=["assignment_status"])

        call_command("reconcile_process_assignments")

        row = ClientServiceProcessAssignment.objects.get(
            client_service_process=self.step_1,
            assignee=self.worker_a,
            is_active=True,
        )
        self.assertEqual(row.acceptance_status, "accepted")
