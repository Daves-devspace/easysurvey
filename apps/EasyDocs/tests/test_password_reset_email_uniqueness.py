from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.EasyDocs.forms import CustomPasswordResetForm


User = get_user_model()


class PasswordResetEmailUniquenessTests(TestCase):
    def test_rejects_duplicate_active_email(self):
        User.objects.create_user(
            username="dup_user_1",
            email="duplicate@example.com",
            password="Pass12345!",
        )
        User.objects.create_user(
            username="dup_user_2",
            email="duplicate@example.com",
            password="Pass12345!",
        )

        form = CustomPasswordResetForm(data={"email": "duplicate@example.com"})

        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_allows_single_active_email(self):
        User.objects.create_user(
            username="single_user",
            email="single@example.com",
            password="Pass12345!",
        )

        form = CustomPasswordResetForm(data={"email": "single@example.com"})

        self.assertTrue(form.is_valid(), form.errors)

    def test_ignores_inactive_duplicate_for_reset(self):
        active_user = User.objects.create_user(
            username="active_user",
            email="mixed@example.com",
            password="Pass12345!",
            is_active=True,
        )
        inactive_user = User.objects.create_user(
            username="inactive_user",
            email="mixed@example.com",
            password="Pass12345!",
            is_active=False,
        )

        self.assertTrue(active_user.is_active)
        self.assertFalse(inactive_user.is_active)

        form = CustomPasswordResetForm(data={"email": "mixed@example.com"})

        self.assertTrue(form.is_valid(), form.errors)
