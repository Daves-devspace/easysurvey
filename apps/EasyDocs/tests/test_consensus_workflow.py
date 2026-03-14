"""
PR-06 tests: consensus workflow engine.

Verifies that ProcessWorkflowService.complete_step:
  - Advances when no active process assignments exist (legacy path).
  - Does NOT advance when accepted assignees have not all completed.
  - Advances when all accepted assignees have completed.
  - Handles the edge case where assignments exist but none are accepted yet.
"""
from unittest.mock import patch

from django.contrib.auth.models import User
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
from apps.EasyDocs.services.process_workflow import ProcessWorkflowService
from apps.notifications.models import Notification


class ConsensusWorkflowEngineTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="wf_admin", password="pass")
        self.worker_a = User.objects.create_user(username="wf_worker_a", password="pass")
        self.worker_b = User.objects.create_user(username="wf_worker_b", password="pass")

        self.service = Service.objects.create(
            name="WF Test Service", category=ServiceCategory.TITLE
        )
        self.proc1 = Process.objects.create(
            service=self.service,
            name="WF Step 1",
            step_order=1,
            cost=0,
            message="Step 1",
            notification_enabled=False,
        )
        self.proc2 = Process.objects.create(
            service=self.service,
            name="WF Step 2",
            step_order=2,
            cost=0,
            message="Step 2",
            notification_enabled=False,
        )

        self.client_obj = Client.objects.create(
            first_name="WF", last_name="Test", phone="0700000888"
        )
        self.cs = ClientService.objects.create(
            client=self.client_obj,
            service=self.service,
            land_description="Plot WF",
            assigned_employee=self.worker_a,
            assignment_status="accepted",
        )

        self.step1 = ClientServiceProcess.objects.get(
            client_service=self.cs, process=self.proc1
        )
        self.step2 = ClientServiceProcess.objects.get(
            client_service=self.cs, process=self.proc2
        )

        # Put step1 in progress so it can be completed
        self.step1.status = "in_progress"
        self.step1.save()

    def _make_assignment(self, step, user, acceptance_status="pending", completion_status="pending"):
        return ClientServiceProcessAssignment.objects.create(
            client_service_process=step,
            assignee=user,
            assigned_by=self.admin,
            is_active=True,
            acceptance_status=acceptance_status,
            completion_status=completion_status,
        )

    # ------------------------------------------------------------------

    def test_legacy_path_no_assignments_advances_step(self):
        """No active assignments → consensus is True → step advances."""
        wf = ProcessWorkflowService(self.cs)
        result = wf.complete_step(self.step1)
        # result is sms_log (None) b/c notifications disabled; but step must be completed
        self.step1.refresh_from_db()
        self.assertEqual(self.step1.status, "completed")

    def test_consensus_not_reached_blocks_advance(self):
        """Two accepted assignees; one has not completed → step stays in_progress."""
        self._make_assignment(self.step1, self.worker_a, acceptance_status="accepted", completion_status="completed")
        self._make_assignment(self.step1, self.worker_b, acceptance_status="accepted", completion_status="pending")

        wf = ProcessWorkflowService(self.cs)
        result = wf.complete_step(self.step1)
        self.assertIsNone(result)  # returns None when blocked
        self.step1.refresh_from_db()
        # Step should NOT be marked completed yet
        self.assertEqual(self.step1.status, "in_progress")

    def test_consensus_reached_advances_step(self):
        """All accepted assignees completed → step advances."""
        self._make_assignment(self.step1, self.worker_a, acceptance_status="accepted", completion_status="completed")
        self._make_assignment(self.step1, self.worker_b, acceptance_status="accepted", completion_status="completed")

        wf = ProcessWorkflowService(self.cs)
        wf.complete_step(self.step1)
        self.step1.refresh_from_db()
        self.assertEqual(self.step1.status, "completed")

    def test_no_accepted_assignees_blocks_advance(self):
        """Active assignments exist but none accepted → consensus False → advance blocked."""
        self._make_assignment(self.step1, self.worker_a, acceptance_status="pending")

        wf = ProcessWorkflowService(self.cs)
        result = wf.complete_step(self.step1)
        self.assertIsNone(result)
        self.step1.refresh_from_db()
        self.assertEqual(self.step1.status, "in_progress")

    def test_single_accepted_and_completed_advances(self):
        """Single accepted+completed assignee → consensus True."""
        self._make_assignment(self.step1, self.worker_a, acceptance_status="accepted", completion_status="completed")

        wf = ProcessWorkflowService(self.cs)
        wf.complete_step(self.step1)
        self.step1.refresh_from_db()
        self.assertEqual(self.step1.status, "completed")

    def test_declined_assignments_are_ignored_in_consensus(self):
        """Declined / inactive assignments don't count toward consensus."""
        # One accepted+completed, one completely declined (inactive)
        self._make_assignment(self.step1, self.worker_a, acceptance_status="accepted", completion_status="completed")
        declined = self._make_assignment(self.step1, self.worker_b, acceptance_status="declined", completion_status="pending")
        declined.is_active = False
        declined.save()

        wf = ProcessWorkflowService(self.cs)
        wf.complete_step(self.step1)
        self.step1.refresh_from_db()
        # Only accepted+completed worker_a matters; should advance
        self.assertEqual(self.step1.status, "completed")

    def test_consensus_reached_advances_to_next_step(self):
        """After step1 advances, step2 should move to in_progress."""
        self._make_assignment(self.step1, self.worker_a, acceptance_status="accepted", completion_status="completed")

        wf = ProcessWorkflowService(self.cs)
        wf.complete_step(self.step1)
        self.step2.refresh_from_db()
        self.assertEqual(self.step2.status, "in_progress")

    @patch("apps.EasyDocs.services.process_workflow.send_push_to_user")
    def test_advancing_step_notifies_next_step_assignees(self, mock_send_push):
        self._make_assignment(
            self.step1,
            self.worker_a,
            acceptance_status="accepted",
            completion_status="completed",
        )
        self._make_assignment(
            self.step2,
            self.worker_b,
            acceptance_status="pending",
            completion_status="pending",
        )

        wf = ProcessWorkflowService(self.cs)
        wf.complete_step(self.step1)

        self.step2.refresh_from_db()
        self.assertEqual(self.step2.status, "in_progress")
        self.assertTrue(
            Notification.objects.filter(
                user=self.worker_b,
                title="Process In Progress",
            ).exists()
        )
        self.assertTrue(mock_send_push.called)
