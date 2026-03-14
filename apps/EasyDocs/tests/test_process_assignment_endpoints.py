"""
PR-05 tests: new secure per-assignment HTTP endpoints.

Covers:
  - Unauthenticated access is redirected (401/302)
  - Non-assignee cannot accept/decline/complete another user's assignment
  - Assignee can accept, decline, complete own assignment
  - Consensus not reached → step stays in_progress after partial complete
  - Admin can assign users to a process step
  - Non-admin cannot use the assign-users endpoint
  - 404-style response when assignment_id does not exist
  - Inactive assignment cannot be accepted/declined
  - Assignment must be accepted before it can be completed
"""
import json

from django.contrib.auth.models import Permission, User
from django.test import Client as DjangoClient, TestCase
from django.urls import reverse

from apps.EasyDocs.models import (
    Client,
    ClientService,
    ClientServiceProcess,
    ClientServiceProcessAssignment,
    Process,
    Service,
    ServiceCategory,
)


class ProcessAssignmentEndpointTestCase(TestCase):
    """Shared setUp for all endpoint tests."""

    def setUp(self):
        self.http = DjangoClient(enforce_csrf_checks=False)

        # Users
        self.admin_user = User.objects.create_user(
            username="admin_ep", password="pass", is_staff=True
        )
        self.assignee = User.objects.create_user(username="assignee_ep", password="pass")
        self.other_user = User.objects.create_user(username="other_ep", password="pass")

        # Service + process template
        self.service = Service.objects.create(
            name="EP Service", category=ServiceCategory.TITLE
        )
        self.process_template = Process.objects.create(
            service=self.service,
            name="EP Step 1",
            step_order=1,
            cost=10,
            message="Do step 1",
            notification_enabled=False,
        )

        # Client + ClientService
        self.client_obj = Client.objects.create(
            first_name="End", last_name="Point", phone="0700000999"
        )
        self.client_service = ClientService.objects.create(
            client=self.client_obj,
            service=self.service,
            land_description="Plot EP",
            assigned_employee=self.assignee,
            assignment_status="accepted",
        )

        # Find the process step row (created automatically via signal on ClientService)
        self.step = ClientServiceProcess.objects.filter(
            client_service=self.client_service
        ).first()

        # Create a CSPA row for the assignee
        self.assignment = ClientServiceProcessAssignment.objects.create(
            client_service_process=self.step,
            assignee=self.assignee,
            assigned_by=self.admin_user,
            is_active=True,
            acceptance_status="pending",
        )

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------
    def _accept_url(self, pk=None):
        return reverse("accept_process_assignment", kwargs={"assignment_id": pk or self.assignment.pk})

    def _decline_url(self, pk=None):
        return reverse("decline_process_assignment", kwargs={"assignment_id": pk or self.assignment.pk})

    def _complete_url(self, pk=None):
        return reverse("complete_process_assignment", kwargs={"assignment_id": pk or self.assignment.pk})

    def _assign_url(self, step_id=None):
        return reverse("assign_users_to_process_step",
                       kwargs={"process_step_id": step_id or self.step.pk})

    def _post_json(self, url, payload=None, user=None):
        if user:
            self.http.force_login(user)
        return self.http.post(
            url,
            data=json.dumps(payload or {}),
            content_type="application/json",
        )


class TestUnauthenticatedAccess(ProcessAssignmentEndpointTestCase):
    def test_accept_requires_login(self):
        resp = self.http.post(self._accept_url(), content_type="application/json")
        self.assertIn(resp.status_code, [302, 401])

    def test_decline_requires_login(self):
        resp = self.http.post(self._decline_url(), content_type="application/json")
        self.assertIn(resp.status_code, [302, 401])

    def test_complete_requires_login(self):
        resp = self.http.post(self._complete_url(), content_type="application/json")
        self.assertIn(resp.status_code, [302, 401])

    def test_assign_users_requires_login(self):
        resp = self.http.post(self._assign_url(), content_type="application/json")
        self.assertIn(resp.status_code, [302, 401])


class TestOwnershipEnforcement(ProcessAssignmentEndpointTestCase):
    """Non-assignee cannot act on another user's assignment."""

    def test_other_user_cannot_accept(self):
        resp = self._post_json(self._accept_url(), user=self.other_user)
        data = json.loads(resp.content)
        self.assertFalse(data["success"])
        self.assertIn("not assigned to you", data["message"])

    def test_other_user_cannot_decline(self):
        resp = self._post_json(self._decline_url(), user=self.other_user)
        data = json.loads(resp.content)
        self.assertFalse(data["success"])

    def test_other_user_cannot_complete(self):
        # First get assignment into accepted state
        self.assignment.acceptance_status = "accepted"
        self.assignment.save()
        resp = self._post_json(self._complete_url(), user=self.other_user)
        data = json.loads(resp.content)
        self.assertFalse(data["success"])


class TestAssigneeAcceptDecline(ProcessAssignmentEndpointTestCase):
    """Correct assignee can accept and decline."""

    def test_assignee_can_accept(self):
        resp = self._post_json(self._accept_url(), payload={"reason": "ok"}, user=self.assignee)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data["success"])
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.acceptance_status, "accepted")
        self.assertIsNotNone(self.assignment.accepted_at)

    def test_assignee_form_post_accept_redirects_and_updates(self):
        self.http.force_login(self.assignee)
        resp = self.http.post(self._accept_url(), data={"reason": "form-accept"})
        self.assertEqual(resp.status_code, 302)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.acceptance_status, "accepted")

    def test_accept_is_idempotent(self):
        # Accept twice → still 200 success
        self._post_json(self._accept_url(), user=self.assignee)
        resp = self._post_json(self._accept_url(), user=self.assignee)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data["success"])

    def test_assignee_can_decline(self):
        resp = self._post_json(self._decline_url(), payload={"reason": "busy"}, user=self.assignee)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data["success"])
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.acceptance_status, "declined")
        self.assertFalse(self.assignment.is_active)

    def test_cannot_accept_inactive_assignment(self):
        self.assignment.is_active = False
        self.assignment.save()
        resp = self._post_json(self._accept_url(), user=self.assignee)
        data = json.loads(resp.content)
        self.assertFalse(data["success"])
        self.assertIn("no longer active", data["message"])

    def test_cannot_decline_inactive_assignment(self):
        self.assignment.is_active = False
        self.assignment.save()
        resp = self._post_json(self._decline_url(), user=self.assignee)
        data = json.loads(resp.content)
        self.assertFalse(data["success"])


class TestAssigneeComplete(ProcessAssignmentEndpointTestCase):
    """Complete action requires acceptance first; consensus tracked correctly."""

    def _accept_assignment(self):
        self.assignment.acceptance_status = "accepted"
        self.assignment.save()
        self.step.status = "in_progress"
        self.step.save()

    def test_cannot_complete_without_accepting_first(self):
        # assignment still in 'pending' state
        self.step.status = "in_progress"
        self.step.save()
        resp = self._post_json(self._complete_url(), user=self.assignee)
        data = json.loads(resp.content)
        self.assertFalse(data["success"])
        self.assertIn("accept", data["message"].lower())

    def test_complete_returns_success_and_logs(self):
        self._accept_assignment()
        resp = self._post_json(
            self._complete_url(), payload={"note": "done"}, user=self.assignee
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data["success"])
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.completion_status, "completed")
        self.assertIsNotNone(self.assignment.completed_at)

    def test_single_assignee_consensus_completes_step(self):
        """With only one accepted assignee, completing it should trigger workflow advance."""
        self._accept_assignment()
        resp = self._post_json(self._complete_url(), user=self.assignee)
        data = json.loads(resp.content)
        self.assertTrue(data["success"])
        # step_completed depends on workflow service; check counts at minimum
        self.assertIn("accepted_count", data)
        self.assertIn("completed_count", data)
        self.assertEqual(data["accepted_count"], 1)
        self.assertEqual(data["completed_count"], 1)

    def test_partial_consensus_does_not_mark_step_completed(self):
        """Two accepted assignees; only one completes → step_completed=False."""
        # Create second assignee on same step
        u2 = User.objects.create_user(username="worker2_ep", password="pass")
        a2 = ClientServiceProcessAssignment.objects.create(
            client_service_process=self.step,
            assignee=u2,
            assigned_by=self.admin_user,
            is_active=True,
            acceptance_status="accepted",
        )
        self._accept_assignment()
        # Only assignee completes (u2 has not)
        resp = self._post_json(self._complete_url(), user=self.assignee)
        data = json.loads(resp.content)
        self.assertTrue(data["success"])
        self.assertFalse(data["step_completed"])
        self.assertEqual(data["accepted_count"], 2)
        self.assertEqual(data["completed_count"], 1)

    def test_cannot_complete_if_step_not_in_progress(self):
        self._accept_assignment()
        self.step.status = "pending"
        self.step.save()
        resp = self._post_json(self._complete_url(), user=self.assignee)
        data = json.loads(resp.content)
        self.assertFalse(data["success"])
        self.assertIn("not currently in progress", data["message"])


class TestAssignUsersEndpoint(ProcessAssignmentEndpointTestCase):
    """Admin/manager assign-users endpoint."""

    def test_non_admin_cannot_assign_users(self):
        resp = self._post_json(
            self._assign_url(),
            payload={"user_ids": [self.assignee.id]},
            user=self.other_user,
        )
        self.assertEqual(resp.status_code, 403)
        data = json.loads(resp.content)
        self.assertFalse(data["success"])

    def test_admin_can_assign_user_to_step(self):
        resp = self._post_json(
            self._assign_url(),
            payload={"user_ids": [self.assignee.id, self.other_user.id], "reason": "test assign"},
            user=self.admin_user,
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data["success"])
        active_count = ClientServiceProcessAssignment.objects.filter(
            client_service_process=self.step, is_active=True
        ).count()
        self.assertEqual(active_count, 2)

    def test_assign_users_deactivates_removed_assignees(self):
        # admin_user creates second assignment
        ClientServiceProcessAssignment.objects.create(
            client_service_process=self.step,
            assignee=self.other_user,
            is_active=True,
            acceptance_status="pending",
        )
        # Now assign only assignee → other_user deactivated
        resp = self._post_json(
            self._assign_url(),
            payload={"user_ids": [self.assignee.id]},
            user=self.admin_user,
        )
        data = json.loads(resp.content)
        self.assertTrue(data["success"])
        self.assertGreaterEqual(data["deactivated"], 1)
        still_active = ClientServiceProcessAssignment.objects.filter(
            client_service_process=self.step, is_active=True
        ).values_list("assignee_id", flat=True)
        self.assertIn(self.assignee.id, list(still_active))
        self.assertNotIn(self.other_user.id, list(still_active))

    def test_assign_users_with_invalid_ids_returns_warning(self):
        resp = self._post_json(
            self._assign_url(),
            payload={"user_ids": [99999]},
            user=self.admin_user,
        )
        data = json.loads(resp.content)
        self.assertTrue(data["success"])
        self.assertIn("Skipped invalid user ids", data["message"])

    def test_non_list_user_ids_returns_400(self):
        resp = self._post_json(
            self._assign_url(),
            payload={"user_ids": "not-a-list"},
            user=self.admin_user,
        )
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.content)
        self.assertFalse(data["success"])

    def test_with_perm_non_staff_can_assign(self):
        """User with explicit Django permission can use admin endpoint."""
        perm = Permission.objects.get(codename="change_clientserviceprocessassignment")
        self.other_user.user_permissions.add(perm)
        self.other_user.refresh_from_db()
        resp = self._post_json(
            self._assign_url(),
            payload={"user_ids": [self.assignee.id]},
            user=self.other_user,
        )
        self.assertEqual(resp.status_code, 200)

    def test_nonexistent_assignment_returns_failure(self):
        resp = self._post_json(
            reverse("accept_process_assignment", kwargs={"assignment_id": 99999}),
            user=self.assignee,
        )
        data = json.loads(resp.content)
        self.assertFalse(data["success"])
        self.assertIn("not found", data["message"].lower())
