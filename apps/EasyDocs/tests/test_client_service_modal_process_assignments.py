from decimal import Decimal

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
from apps.EasyDocs.services.process_assignments import sync_service_assignment_to_process_assignments


class ClientServiceModalProcessAssignmentTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(
            first_name="Modal",
            last_name="Client",
            phone="0700000700",
        )

        self.worker_a = self._create_user("modal_worker_a")
        self.worker_b = self._create_user("modal_worker_b")
        self.worker_c = self._create_user("modal_worker_c")

        self.title_service = Service.objects.create(
            name="Modal Title Service",
            category=ServiceCategory.TITLE,
            total_price=0,
        )
        self.step_1_template = Process.objects.create(
            service=self.title_service,
            name="Step 1",
            step_order=1,
            cost=10,
            message="Step 1",
            notification_enabled=False,
        )
        self.step_2_template = Process.objects.create(
            service=self.title_service,
            name="Step 2",
            step_order=2,
            cost=20,
            message="Step 2",
            notification_enabled=False,
        )

        self.simple_service = Service.objects.create(
            name="Simple Service",
            category=ServiceCategory.OTHERS,
            total_price=150,
        )

    @staticmethod
    def _create_user(username):
        from django.contrib.auth.models import User
        from apps.Employee.models import EmployeeProfile

        user = User.objects.create_user(username=username, password="pass")
        EmployeeProfile.objects.create(user=user)
        return user

    def _post_url(self):
        return reverse("client-service", kwargs={"client_id": self.client_obj.id})

    def test_add_service_applies_per_process_assignees(self):
        response = self.client.post(
            self._post_url(),
            {
                "add_client_service": "1",
                "client": str(self.client_obj.id),
                "category": ServiceCategory.TITLE,
                "service": str(self.title_service.id),
                "land_description": "Plot Modal",
                "process_id[]": [str(self.step_1_template.id), str(self.step_2_template.id)],
                "process_cost[]": ["10", "20"],
                f"process_assignees_{self.step_1_template.id}[]": [str(self.worker_a.id)],
                f"process_assignees_{self.step_2_template.id}[]": [str(self.worker_b.id), str(self.worker_c.id)],
            },
        )

        self.assertEqual(response.status_code, 302)

        cs = ClientService.objects.filter(
            client=self.client_obj,
            service=self.title_service,
        ).latest("id")

        self.assertEqual(cs.assigned_employee_id, self.worker_a.id)

        step_1 = ClientServiceProcess.objects.get(
            client_service=cs,
            process=self.step_1_template,
        )
        step_2 = ClientServiceProcess.objects.get(
            client_service=cs,
            process=self.step_2_template,
        )

        step_1_assignees = set(
            ClientServiceProcessAssignment.objects.filter(
                client_service_process=step_1,
                is_active=True,
            ).values_list("assignee_id", flat=True)
        )
        step_2_assignees = set(
            ClientServiceProcessAssignment.objects.filter(
                client_service_process=step_2,
                is_active=True,
            ).values_list("assignee_id", flat=True)
        )

        self.assertEqual(step_1_assignees, {self.worker_a.id})
        self.assertEqual(step_2_assignees, {self.worker_b.id, self.worker_c.id})

    def test_service_without_processes_requires_full_service_assignee(self):
        response = self.client.post(
            self._post_url(),
            {
                "add_client_service": "1",
                "client": str(self.client_obj.id),
                "category": ServiceCategory.OTHERS,
                "service": str(self.simple_service.id),
                "land_description": "No process service",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            ClientService.objects.filter(
                client=self.client_obj,
                service=self.simple_service,
            ).exists()
        )

    def test_add_service_without_processes_persists_override_total_price_key(self):
        response = self.client.post(
            self._post_url(),
            {
                "add_client_service": "1",
                "client": str(self.client_obj.id),
                "category": ServiceCategory.OTHERS,
                "service": str(self.simple_service.id),
                "assigned_employee": str(self.worker_a.id),
                "land_description": "No process service with client price",
                "override_total_price": "3450.50",
            },
        )

        self.assertEqual(response.status_code, 302)

        cs = ClientService.objects.filter(
            client=self.client_obj,
            service=self.simple_service,
        ).latest("id")

        self.assertEqual(cs.overridden_total_price, Decimal("3450.50"))

    def test_edit_service_applies_explicit_and_fallback_process_assignees(self):
        client_service = ClientService.objects.create(
            client=self.client_obj,
            service=self.title_service,
            land_description="Initial modal edit",
            assigned_employee=self.worker_a,
            assignment_status="pending_acceptance",
        )
        sync_service_assignment_to_process_assignments(
            client_service=client_service,
            assigned_employee=self.worker_a,
            assigned_by=None,
            reason="seed",
        )

        response = self.client.post(
            self._post_url(),
            {
                "client_service_id": str(client_service.id),
                "client": str(self.client_obj.id),
                "category": ServiceCategory.TITLE,
                "service": str(self.title_service.id),
                "assigned_employee": str(self.worker_c.id),
                "land_description": "Edited modal service",
                "process_id[]": [str(self.step_1_template.id), str(self.step_2_template.id)],
                "process_cost[]": ["10", "20"],
                f"process_assignees_{self.step_1_template.id}[]": [str(self.worker_b.id)],
            },
        )

        self.assertEqual(response.status_code, 302)

        client_service.refresh_from_db()
        self.assertEqual(client_service.assigned_employee_id, self.worker_c.id)

        step_1 = ClientServiceProcess.objects.get(
            client_service=client_service,
            process=self.step_1_template,
        )
        step_2 = ClientServiceProcess.objects.get(
            client_service=client_service,
            process=self.step_2_template,
        )

        step_1_assignees = set(
            ClientServiceProcessAssignment.objects.filter(
                client_service_process=step_1,
                is_active=True,
            ).values_list("assignee_id", flat=True)
        )
        step_2_assignees = set(
            ClientServiceProcessAssignment.objects.filter(
                client_service_process=step_2,
                is_active=True,
            ).values_list("assignee_id", flat=True)
        )

        self.assertEqual(step_1_assignees, {self.worker_b.id})
        self.assertEqual(step_2_assignees, {self.worker_c.id})
