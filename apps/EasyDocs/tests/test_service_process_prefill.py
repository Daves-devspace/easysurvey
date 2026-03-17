from django.contrib.auth.models import User
from django.test import TestCase
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


class ServiceProcessPrefillEndpointTests(TestCase):
    def setUp(self):
        self.worker_x = User.objects.create_user(username="worker_x_prefill", password="pass")
        self.worker_y = User.objects.create_user(username="worker_y_prefill", password="pass")

        self.client_obj = Client.objects.create(
            first_name="David",
            last_name="Maina",
            phone="0115429140",
        )

        self.service = Service.objects.create(
            name="Land transfer",
            category=ServiceCategory.TITLE,
        )
        self.process1 = Process.objects.create(
            service=self.service,
            name="Process 1",
            step_order=1,
            cost=100,
            message="P1",
            notification_enabled=False,
        )
        self.process2 = Process.objects.create(
            service=self.service,
            name="Process 2",
            step_order=2,
            cost=200,
            message="P2",
            notification_enabled=False,
        )

    def _ensure_steps(self, client_service):
        steps = list(
            client_service.service_processes.select_related("process").order_by("process__step_order")
        )
        if steps:
            return steps

        step1 = ClientServiceProcess.objects.create(
            client_service=client_service,
            process=self.process1,
            status="in_progress",
        )
        step2 = ClientServiceProcess.objects.create(
            client_service=client_service,
            process=self.process2,
            status="pending",
        )
        return [step1, step2]

    def _make_client_service(self, *, assignee, land_description, client_obj=None):
        target_client = client_obj or self.client_obj
        cs = ClientService.objects.create(
            client=target_client,
            service=self.service,
            land_description=land_description,
            assigned_employee=assignee,
            assignment_status="accepted",
        )
        self._ensure_steps(cs)
        return cs

    def _assign_first_step(self, client_service, user):
        steps = self._ensure_steps(client_service)
        first_step = steps[0]
        ClientServiceProcessAssignment.objects.create(
            client_service_process=first_step,
            assignee=user,
            assigned_by=user,
            is_active=True,
            acceptance_status="accepted",
        )

    def test_prefill_uses_latest_similar_service_assignments(self):
        old_cs = self._make_client_service(
            assignee=self.worker_x,
            land_description="Plot A",
        )
        self._assign_first_step(old_cs, self.worker_x)

        url = reverse("get_service_processes", kwargs={"service_id": self.service.id})
        response = self.client.get(url, {"client_id": self.client_obj.id})

        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload.get("suggested_default_assignee_id"), self.worker_x.id)
        self.assertEqual(
            payload.get("suggested_assignee_map", {}).get(str(self.process1.id)),
            [self.worker_x.id],
        )
        self.assertEqual(
            payload.get("suggested_assignee_map", {}).get(str(self.process2.id)),
            [],
        )

    def test_prefill_can_exclude_current_client_service(self):
        old_cs = self._make_client_service(
            assignee=self.worker_x,
            land_description="Plot A",
        )
        self._assign_first_step(old_cs, self.worker_x)

        current_cs = self._make_client_service(
            assignee=self.worker_y,
            land_description="Plot B",
        )
        self._assign_first_step(current_cs, self.worker_y)

        url = reverse("get_service_processes", kwargs={"service_id": self.service.id})
        response = self.client.get(
            url,
            {
                "client_id": self.client_obj.id,
                "exclude_client_service_id": current_cs.id,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("suggested_default_assignee_id"), self.worker_x.id)
        self.assertEqual(
            payload.get("suggested_assignee_map", {}).get(str(self.process1.id)),
            [self.worker_x.id],
        )

    def test_prefill_without_global_fallback_returns_empty_when_no_client_history(self):
        other_client = Client.objects.create(
            first_name="Jane",
            last_name="Doe",
            phone="0700000001",
        )
        other_cs = self._make_client_service(
            assignee=self.worker_x,
            land_description="Other Plot",
            client_obj=other_client,
        )
        self._assign_first_step(other_cs, self.worker_x)

        url = reverse("get_service_processes", kwargs={"service_id": self.service.id})
        response = self.client.get(url, {"client_id": self.client_obj.id})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsNone(payload.get("suggested_default_assignee_id"))
        self.assertEqual(payload.get("suggested_assignee_map"), {})

    def test_prefill_with_global_fallback_uses_latest_other_client(self):
        other_client_a = Client.objects.create(
            first_name="Alice",
            last_name="A",
            phone="0700000002",
        )
        other_client_b = Client.objects.create(
            first_name="Brian",
            last_name="B",
            phone="0700000003",
        )

        older_cs = self._make_client_service(
            assignee=self.worker_x,
            land_description="Older Plot",
            client_obj=other_client_a,
        )
        self._assign_first_step(older_cs, self.worker_x)

        latest_cs = self._make_client_service(
            assignee=self.worker_y,
            land_description="Latest Plot",
            client_obj=other_client_b,
        )
        self._assign_first_step(latest_cs, self.worker_y)

        url = reverse("get_service_processes", kwargs={"service_id": self.service.id})
        response = self.client.get(
            url,
            {
                "client_id": self.client_obj.id,
                "global_fallback": "1",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("suggested_default_assignee_id"), self.worker_y.id)
        self.assertEqual(
            payload.get("suggested_assignee_map", {}).get(str(self.process1.id)),
            [self.worker_y.id],
        )
