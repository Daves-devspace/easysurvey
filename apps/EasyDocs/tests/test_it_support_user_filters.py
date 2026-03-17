from django.contrib.auth.models import User
from django.test import TestCase

from apps.EasyDocs.forms import ClientServiceForm, ExpenseForm
from apps.Employee.models import EmployeeProfile


class ITSupportUserFilterTests(TestCase):
    def setUp(self):
        self.front_office = User.objects.create_user(
            username="front_office_user",
            password="Pass12345!",
            first_name="Front",
            last_name="Office",
        )
        EmployeeProfile.objects.create(
            user=self.front_office,
            role=EmployeeProfile.RoleChoices.FRONTOFFICE,
        )

        self.it_support = User.objects.create_user(
            username="it_support_user",
            password="Pass12345!",
            first_name="IT",
            last_name="Support",
        )
        EmployeeProfile.objects.create(
            user=self.it_support,
            role=EmployeeProfile.RoleChoices.IT_SUPPORT,
        )

    def test_client_service_form_excludes_it_support_from_assignee_options(self):
        form = ClientServiceForm()

        assignee_ids = set(form.fields["assigned_employee"].queryset.values_list("id", flat=True))

        self.assertIn(self.front_office.id, assignee_ids)
        self.assertNotIn(self.it_support.id, assignee_ids)

    def test_expense_form_excludes_it_support_from_recorded_by_options(self):
        form = ExpenseForm(current_user=self.front_office)

        recorder_ids = set(form.fields["recorded_by"].queryset.values_list("id", flat=True))

        self.assertIn(self.front_office.id, recorder_ids)
        self.assertNotIn(self.it_support.id, recorder_ids)

    def test_expense_form_does_not_prefill_it_support_as_recorder(self):
        form = ExpenseForm(current_user=self.it_support)

        self.assertNotEqual(form.initial.get("recorded_by"), self.it_support.id)

    def test_expense_form_prefills_allowed_current_user_as_recorder(self):
        form = ExpenseForm(current_user=self.front_office)

        self.assertEqual(form.initial.get("recorded_by"), self.front_office.id)
