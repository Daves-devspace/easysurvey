"""
Tests for the allow_task_assigning and allow_document_assigning feature toggles.

Covers:
  - feature_flags helpers: correct True/False results based on SiteSettings
  - TaskManagementView.dispatch: redirects to home when task assigning is OFF
  - accept_document_handoff / decline_document_handoff: 403 JSON when document assigning is OFF
  - assign_document_handoff: redirect with error when document assigning is OFF
  - Process assignment endpoints (accept/decline/complete/assign): 403 JSON when task assigning is OFF
  - _apply_process_level_assignments: no ClientServiceProcessAssignment rows created when toggle is OFF
"""
import json

from django.contrib.auth.models import User
from django.test import Client as DjangoClient, TestCase
from django.urls import reverse

from apps.EasyDocs.models import (
    Client,
    ClientService,
    ClientServiceProcess,
    ClientServiceProcessAssignment,
    DocumentHandoff,
    Process,
    Service,
    ServiceCategory,
    SiteSettings,
)
from apps.EasyDocs.services.feature_flags import (
    is_document_assigning_enabled,
    is_task_assigning_enabled,
)


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

def _make_settings(**kwargs):
    """Create or update the singleton SiteSettings row."""
    settings, _ = SiteSettings.objects.get_or_create(singleton_enforcer=True)
    for field, value in kwargs.items():
        setattr(settings, field, value)
    settings.save()
    return settings


# ---------------------------------------------------------------------------
# 1. feature_flags module unit tests
# ---------------------------------------------------------------------------

class FeatureFlagHelperTests(TestCase):
    """Direct unit tests for is_task_assigning_enabled / is_document_assigning_enabled."""

    def test_task_flag_false_when_no_settings_row(self):
        # Clean DB: no SiteSettings → falls back to SiteSettings() with default=False
        self.assertFalse(is_task_assigning_enabled())

    def test_document_flag_false_when_no_settings_row(self):
        self.assertFalse(is_document_assigning_enabled())

    def test_task_flag_false_when_explicitly_disabled(self):
        _make_settings(allow_task_assigning=False, allow_document_assigning=False)
        self.assertFalse(is_task_assigning_enabled())

    def test_document_flag_false_when_explicitly_disabled(self):
        _make_settings(allow_task_assigning=False, allow_document_assigning=False)
        self.assertFalse(is_document_assigning_enabled())

    def test_task_flag_true_when_enabled(self):
        _make_settings(allow_task_assigning=True)
        self.assertTrue(is_task_assigning_enabled())

    def test_document_flag_true_when_enabled(self):
        _make_settings(allow_document_assigning=True)
        self.assertTrue(is_document_assigning_enabled())

    def test_flags_are_independent(self):
        _make_settings(allow_task_assigning=True, allow_document_assigning=False)
        self.assertTrue(is_task_assigning_enabled())
        self.assertFalse(is_document_assigning_enabled())

    def test_both_flags_enabled(self):
        _make_settings(allow_task_assigning=True, allow_document_assigning=True)
        self.assertTrue(is_task_assigning_enabled())
        self.assertTrue(is_document_assigning_enabled())


# ---------------------------------------------------------------------------
# 2. TaskManagementView dispatch guard
# ---------------------------------------------------------------------------

class TaskManagementViewToggleTests(TestCase):
    """TaskManagementView.dispatch redirects to home when toggle is OFF."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="toggle_admin", password="pass"
        )
        self.url = reverse("task-management")

    def test_redirects_when_toggle_off(self):
        # No SiteSettings row → toggle is OFF by default
        self.client.force_login(self.user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("home"), resp["Location"])

    def test_redirect_carries_error_message(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url, follow=True)
        messages_text = " ".join(str(m) for m in resp.context["messages"])
        self.assertIn("disabled", messages_text.lower())

    def test_passes_through_when_toggle_on(self):
        _make_settings(allow_task_assigning=True)
        self.client.force_login(self.user)
        resp = self.client.get(self.url)
        # Guard is not tripped; response is a real page render, not a toggle redirect
        self.assertNotEqual(resp.status_code, 302)


# ---------------------------------------------------------------------------
# 3. accept_document_handoff / decline_document_handoff toggle guards
# ---------------------------------------------------------------------------

class DocumentHandoffEndpointToggleTests(TestCase):
    """
    accept_document_handoff and decline_document_handoff return 403 JSON
    when document assigning is OFF.  The toggle guard fires before any DB
    lookup so a non-existent handoff_id is sufficient for the OFF tests.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="handoff_user", password="pass")
        # 9999 is a deliberately absent PK – guard fires first when toggle is OFF
        self.accept_url = reverse("accept_document_handoff", kwargs={"handoff_id": 9999})
        self.decline_url = reverse("decline_document_handoff", kwargs={"handoff_id": 9999})

    def _post_json(self, url):
        self.client.force_login(self.user)
        return self.client.post(url, content_type="application/json", data=json.dumps({}))

    # ---- toggle OFF ---------------------------------------------------------

    def test_accept_returns_403_when_toggle_off(self):
        resp = self._post_json(self.accept_url)
        self.assertEqual(resp.status_code, 403)
        data = json.loads(resp.content)
        self.assertFalse(data["success"])
        self.assertIn("disabled", data["message"].lower())

    def test_decline_returns_403_when_toggle_off(self):
        resp = self._post_json(self.decline_url)
        self.assertEqual(resp.status_code, 403)
        data = json.loads(resp.content)
        self.assertFalse(data["success"])
        self.assertIn("disabled", data["message"].lower())

    # ---- toggle ON ----------------------------------------------------------

    def test_accept_passes_guard_when_toggle_on(self):
        """With toggle ON the guard is bypassed; a missing handoff → non-403 error."""
        _make_settings(allow_document_assigning=True)
        resp = self._post_json(self.accept_url)
        # Whatever the downstream result, it must NOT be the toggle 403
        self.assertNotEqual(resp.status_code, 403)

    def test_decline_passes_guard_when_toggle_on(self):
        _make_settings(allow_document_assigning=True)
        resp = self._post_json(self.decline_url)
        self.assertNotEqual(resp.status_code, 403)


# ---------------------------------------------------------------------------
# 4. assign_document_handoff toggle guard
# ---------------------------------------------------------------------------

class AssignDocumentHandoffToggleTests(TestCase):
    """
    assign_document_handoff redirects immediately (before any DB writes) when
    document assigning is OFF.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="assign_doc_user", password="pass")
        self.url = reverse("assign_document_handoff")

    def _post(self, **extra_data):
        self.client.force_login(self.user)
        payload = {"doc_kind": "office", "document_id": "1", "assigned_to": "1"}
        payload.update(extra_data)
        return self.client.post(self.url, data=payload)

    def test_redirects_when_toggle_off(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 302)

    def test_no_handoff_created_when_toggle_off(self):
        self._post()
        self.assertFalse(DocumentHandoff.objects.exists())

    def test_error_message_set_when_toggle_off(self):
        resp = self._post(HTTP_REFERER="/documents/")
        # Follow the redirect and check for the error message in rendered output
        self.client.force_login(self.user)
        resp_followed = self.client.post(self.url, data={
            "doc_kind": "office", "document_id": "1", "assigned_to": "1",
        }, follow=True)
        messages_text = " ".join(str(m) for m in resp_followed.context["messages"])
        self.assertIn("disabled", messages_text.lower())


# ---------------------------------------------------------------------------
# 5. Process assignment HTTP endpoints toggle guard
# ---------------------------------------------------------------------------

class ProcessAssignmentEndpointToggleTests(TestCase):
    """
    All four process-assignment action endpoints return 403 JSON when task
    assigning is disabled, leaving DB state unchanged.
    """

    def setUp(self):
        self.http = DjangoClient(enforce_csrf_checks=False)
        self.user = User.objects.create_user(username="proc_toggle_user", password="pass", is_staff=True)

        # Minimal fixture so URLs resolve to real PKs
        service = Service.objects.create(
            name="Toggle Svc", category=ServiceCategory.TITLE
        )
        process_template = Process.objects.create(
            service=service,
            name="Toggle Step",
            step_order=1,
            cost=10,
            message="step",
            notification_enabled=False,
        )
        client_obj = Client.objects.create(
            first_name="Toggle", last_name="Client", phone="0700001111"
        )
        client_service = ClientService.objects.create(
            client=client_obj,
            service=service,
            land_description="Toggle Plot",
            assigned_employee=self.user,
            assignment_status="accepted",
        )
        # Use the signal-created step if it exists, otherwise create one
        step = ClientServiceProcess.objects.filter(
            client_service=client_service
        ).first()
        if step is None:
            step = ClientServiceProcess.objects.create(
                client_service=client_service,
                process=process_template,
                status="in_progress",
            )
        self.step = step
        self.assignment = ClientServiceProcessAssignment.objects.create(
            client_service_process=step,
            assignee=self.user,
            assigned_by=self.user,
            is_active=True,
            acceptance_status="pending",
        )

    def _post_json(self, url):
        self.http.force_login(self.user)
        return self.http.post(url, content_type="application/json", data=json.dumps({}))

    # ---- toggle OFF ---------------------------------------------------------

    def test_accept_blocked_when_toggle_off(self):
        url = reverse("accept_process_assignment", kwargs={"assignment_id": self.assignment.pk})
        resp = self._post_json(url)
        self.assertEqual(resp.status_code, 403)
        data = json.loads(resp.content)
        self.assertFalse(data["success"])
        self.assertIn("disabled", data["message"].lower())

    def test_accept_does_not_mutate_db_when_toggle_off(self):
        url = reverse("accept_process_assignment", kwargs={"assignment_id": self.assignment.pk})
        self._post_json(url)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.acceptance_status, "pending")

    def test_decline_blocked_when_toggle_off(self):
        url = reverse("decline_process_assignment", kwargs={"assignment_id": self.assignment.pk})
        resp = self._post_json(url)
        self.assertEqual(resp.status_code, 403)
        data = json.loads(resp.content)
        self.assertFalse(data["success"])

    def test_complete_blocked_when_toggle_off(self):
        url = reverse("complete_process_assignment", kwargs={"assignment_id": self.assignment.pk})
        resp = self._post_json(url)
        self.assertEqual(resp.status_code, 403)
        data = json.loads(resp.content)
        self.assertFalse(data["success"])

    def test_assign_users_blocked_when_toggle_off(self):
        url = reverse("assign_users_to_process_step", kwargs={"process_step_id": self.step.pk})
        resp = self._post_json(url)
        self.assertEqual(resp.status_code, 403)
        data = json.loads(resp.content)
        self.assertFalse(data["success"])

    # ---- toggle ON ----------------------------------------------------------

    def test_accept_passes_guard_when_toggle_on(self):
        """Toggle ON: guard is bypassed, normal logic runs (may succeed or fail for other reasons)."""
        _make_settings(allow_task_assigning=True)
        url = reverse("accept_process_assignment", kwargs={"assignment_id": self.assignment.pk})
        resp = self._post_json(url)
        self.assertNotEqual(resp.status_code, 403)

    def test_assign_users_passes_guard_when_toggle_on(self):
        _make_settings(allow_task_assigning=True)
        url = reverse("assign_users_to_process_step", kwargs={"process_step_id": self.step.pk})
        resp = self._post_json(url)
        self.assertNotEqual(resp.status_code, 403)


# ---------------------------------------------------------------------------
# 6. _apply_process_level_assignments skips DB writes when toggle is OFF
# ---------------------------------------------------------------------------

class ProcessAssignmentSkippedWhenToggleOffTests(TestCase):
    """
    Adding a client service with explicit per-process assignees must NOT create
    ClientServiceProcessAssignment rows when task assigning is disabled.
    When the toggle is ON the rows should be created as normal.
    """

    def setUp(self):
        from apps.Employee.models import EmployeeProfile

        self.worker = User.objects.create_user(username="proc_worker_tog", password="pass")
        EmployeeProfile.objects.create(user=self.worker)

        # Admin user to submit the form (no permission barriers)
        self.admin = User.objects.create_superuser(username="proc_admin_tog", password="pass")

        self.client_obj = Client.objects.create(
            first_name="Toggle", last_name="Svc", phone="0700002222"
        )
        self.service = Service.objects.create(
            name="Toggle Title Svc", category=ServiceCategory.TITLE, total_price=0
        )
        self.process_template = Process.objects.create(
            service=self.service,
            name="Toggle Process Step",
            step_order=1,
            cost=10,
            message="step",
            notification_enabled=False,
        )

    def _post_add_service(self):
        url = reverse("client-service", kwargs={"client_id": self.client_obj.id})
        return self.client.post(url, {
            "add_client_service": "1",
            "client": str(self.client_obj.id),
            "category": ServiceCategory.TITLE,
            "service": str(self.service.id),
            "land_description": "Toggle plot",
            "process_id[]": [str(self.process_template.id)],
            "process_cost[]": ["10"],
            f"process_assignees_{self.process_template.id}[]": [str(self.worker.id)],
        })

    def test_no_process_assignments_created_when_toggle_off(self):
        # Default: no SiteSettings → toggle OFF
        self.client.force_login(self.admin)
        resp = self._post_add_service()
        # Redirect expected (service creation completes but assignment is skipped)
        self.assertEqual(resp.status_code, 302)
        count = ClientServiceProcessAssignment.objects.filter(
            assignee=self.worker
        ).count()
        self.assertEqual(count, 0)

    def test_process_assignments_created_when_toggle_on(self):
        _make_settings(allow_task_assigning=True)
        self.client.force_login(self.admin)
        resp = self._post_add_service()
        self.assertEqual(resp.status_code, 302)
        count = ClientServiceProcessAssignment.objects.filter(
            assignee=self.worker
        ).count()
        self.assertGreater(count, 0)
