from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.EasyDocs.models import ScheduledTask
from apps.EasyDocs.tasks import dispatch_due_scheduled_tasks


class ReminderDispatchTests(TestCase):
    def test_dispatch_routes_dynamic_reminder_task_names(self):
        task = ScheduledTask.objects.create(
            task_id="reminder_100_50",
            task_name="50% Reminder: Title Workflow",
            task_type="reminder",
            scheduled_time=timezone.now(),
            status="pending",
            payload={
                "client_service_id": 100,
                "employee_id": 200,
                "percentage": 50,
                "deadline": "2026-03-15T08:00:00+03:00",
                "message": "REMINDER (50%): Service deadline approaching.",
            },
        )

        with patch(
            "apps.EasyDocs.tasks.send_service_deadline_reminder.apply_async",
            return_value=SimpleNamespace(id="celery-rem-1"),
        ) as mocked_apply_async:
            dispatch_due_scheduled_tasks()

        task.refresh_from_db()
        self.assertEqual(task.status, "sent")
        self.assertEqual(task.task_id, "celery-rem-1")
        mocked_apply_async.assert_called_once_with(kwargs=task.payload)

    def test_dispatch_marks_unknown_non_reminder_as_failed(self):
        task = ScheduledTask.objects.create(
            task_id="unknown_1",
            task_name="some_unknown_task_name",
            task_type="other",
            scheduled_time=timezone.now(),
            status="pending",
            payload={"foo": "bar"},
        )

        dispatch_due_scheduled_tasks()

        task.refresh_from_db()
        self.assertEqual(task.status, "failed")
        self.assertIn("No dispatcher found", task.notes)
