from django.contrib.auth import authenticate, get_user_model
from django.test import TestCase

from apps.Employee.forms import (
    EmployeeProfileForm,
    EmployeeProfileUpdateForm,
    UnifiedEmployeeProfileForm,
)
from apps.Employee.models import EmployeeProfile


User = get_user_model()


class EmployeeProfileFormPasswordSafetyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="jdoe",
            email="john@example.com",
            password="OldPassword123!",
            first_name="John",
            last_name="Doe",
        )
        self.profile = EmployeeProfile.objects.create(
            user=self.user,
            role=EmployeeProfile.RoleChoices.FRONTOFFICE,
        )

    def _reset_password(self):
        self.user.set_password("NewPassword123!")
        self.user.save(update_fields=["password"])

    def test_unified_profile_form_does_not_overwrite_new_password(self):
        stale_profile = EmployeeProfile.objects.select_related("user").get(pk=self.profile.pk)

        self._reset_password()

        form = UnifiedEmployeeProfileForm(
            data={
                "username": "jdoe",
                "first_name": "John",
                "last_name": "Doe",
                "email": "john@example.com",
                "phone_number": "0700000000",
                "address": "Nairobi",
                "department": "Operations",
                "role": EmployeeProfile.RoleChoices.FRONTOFFICE,
            },
            instance=stale_profile,
        )

        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        authenticated = authenticate(username="jdoe", password="NewPassword123!")
        self.assertIsNotNone(authenticated)
        self.assertEqual(authenticated.pk, self.user.pk)

    def test_employee_profile_update_form_does_not_overwrite_new_password(self):
        stale_profile = EmployeeProfile.objects.select_related("user").get(pk=self.profile.pk)
        admin_user = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="AdminPassword123!",
        )

        self._reset_password()

        form = EmployeeProfileUpdateForm(
            data={
                "username": "jdoe",
                "first_name": "John",
                "last_name": "Doe",
                "email": "john@example.com",
                "phone_number": "0700000000",
                "address": "Nairobi",
                "profile_picture": "",
                "role": EmployeeProfile.RoleChoices.FRONTOFFICE,
                "department": "Operations",
            },
            instance=stale_profile,
            user=admin_user,
        )

        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        authenticated = authenticate(username="jdoe", password="NewPassword123!")
        self.assertIsNotNone(authenticated)
        self.assertEqual(authenticated.pk, self.user.pk)

    def test_employee_profile_form_update_does_not_overwrite_new_password(self):
        stale_profile = EmployeeProfile.objects.select_related("user").get(pk=self.profile.pk)

        self._reset_password()

        form = EmployeeProfileForm(
            data={
                "first_name": "John",
                "last_name": "Doe",
                "email": "john@example.com",
                "phone_number": "0700000000",
                "department": "Operations",
                "address": "Nairobi",
                "role": EmployeeProfile.RoleChoices.FRONTOFFICE,
            },
            instance=stale_profile,
        )

        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        authenticated = authenticate(username="john", password="NewPassword123!")
        self.assertIsNotNone(authenticated)
        self.assertEqual(authenticated.pk, self.user.pk)


class EmployeeProfileEmailUniquenessTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(
            username="alpha",
            email="alpha@example.com",
            password="AlphaPass123!",
            first_name="Alpha",
            last_name="User",
        )
        self.profile_a = EmployeeProfile.objects.create(
            user=self.user_a,
            role=EmployeeProfile.RoleChoices.FRONTOFFICE,
        )

        self.user_b = User.objects.create_user(
            username="bravo",
            email="bravo@example.com",
            password="BravoPass123!",
            first_name="Bravo",
            last_name="User",
        )
        self.profile_b = EmployeeProfile.objects.create(
            user=self.user_b,
            role=EmployeeProfile.RoleChoices.FRONTOFFICE,
        )

    def test_unified_profile_form_rejects_existing_email(self):
        form = UnifiedEmployeeProfileForm(
            data={
                "username": "bravo",
                "first_name": "Bravo",
                "last_name": "User",
                "email": "ALPHA@example.com",
                "phone_number": "0700000002",
                "address": "Nairobi",
                "department": "Operations",
                "role": EmployeeProfile.RoleChoices.FRONTOFFICE,
            },
            instance=self.profile_b,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_employee_profile_update_form_rejects_existing_email(self):
        admin_user = User.objects.create_superuser(
            username="admin2",
            email="admin2@example.com",
            password="AdminPass123!",
        )

        form = EmployeeProfileUpdateForm(
            data={
                "username": "bravo",
                "first_name": "Bravo",
                "last_name": "User",
                "email": "alpha@example.com",
                "phone_number": "0700000003",
                "address": "Nairobi",
                "profile_picture": "",
                "role": EmployeeProfile.RoleChoices.FRONTOFFICE,
                "department": "Operations",
            },
            instance=self.profile_b,
            user=admin_user,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_employee_profile_form_rejects_existing_email_on_create(self):
        form = EmployeeProfileForm(
            data={
                "first_name": "Charlie",
                "last_name": "User",
                "email": "alpha@example.com",
                "phone_number": "0700000004",
                "department": "Ops",
                "address": "Nairobi",
                "role": EmployeeProfile.RoleChoices.FRONTOFFICE,
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)
