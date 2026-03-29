from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from django_tenants.test.cases import TenantTestCase
from django_tenants.utils import schema_context

from apps.Employee.models import EmployeeProfile
from apps.tenants.models import Company, Domain


User = get_user_model()


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class UserManagementSupportPolicyTests(TenantTestCase):
    @classmethod
    def setUpClass(cls):
        with schema_context('public'):
            Domain.objects.filter(domain=cls.get_test_tenant_domain()).delete()
            existing_tenants = Company.objects_with_deleted.filter(
                schema_name=cls.get_test_schema_name(),
            ) | Company.objects_with_deleted.filter(
                slug=f'{cls.get_test_schema_name()}-tenant',
            ) | Company.objects_with_deleted.filter(
                name=f'{cls.get_test_schema_name()} Tenant',
            )
            for existing_tenant in existing_tenants.distinct():
                existing_tenant.delete(force=True, force_drop=True)

        super().setUpClass()

    @classmethod
    def get_test_schema_name(cls):
        return 'employee_support_policy_test'

    @classmethod
    def get_test_tenant_domain(cls):
        return 'employee-support-policy.test.com'

    @classmethod
    def setup_tenant(cls, tenant):
        tenant.name = f'{cls.get_test_schema_name()} Tenant'
        tenant.slug = f'{cls.get_test_schema_name()}-tenant'
        tenant.admin_email = 'admin@example.com'
        tenant.bootstrap_it_email = 'support@example.com'
        tenant.bootstrap_it_name = 'IT Support'
        tenant.support_access_mode = Company.SupportAccessMode.ON_REQUEST

    def setUp(self):
        self.client.defaults['HTTP_HOST'] = self.get_test_tenant_domain()

        self.admin_user = User.objects.create_superuser(
            username='tenant_admin',
            email='tenant_admin@example.com',
            password='AdminPass123!',
        )
        admin_profile, _ = EmployeeProfile.objects.get_or_create(
            user=self.admin_user,
        )
        admin_profile.role = EmployeeProfile.RoleChoices.ADMIN
        admin_profile.save(update_fields=['role'])

        self.it_support_user = User.objects.create_user(
            username='tenant_support',
            email='tenant_support@example.com',
            password='SupportPass123!',
            is_active=False,
        )
        self.it_support_profile, _ = EmployeeProfile.objects.get_or_create(
            user=self.it_support_user,
        )
        self.it_support_profile.role = EmployeeProfile.RoleChoices.IT_SUPPORT
        self.it_support_profile.force_password_reset = False
        self.it_support_profile.save(update_fields=['role', 'force_password_reset'])

        self.url = reverse('users-update')
        self.client.force_login(self.admin_user)

    def _public_company(self):
        with schema_context('public'):
            return Company.objects_with_deleted.get(schema_name=self.get_test_schema_name())

    @patch('apps.Employee.admin_views.CustomPasswordResetForm.save')
    def test_grant_support_access_sets_window_enables_it_user_and_flags_for_reset(self, mock_save):
        before = timezone.now()

        response = self.client.post(
            self.url,
            {
                'grant_support_access': '1',
                'support_access_hours': '24',
                'support_access_reason': 'Debugging onboarding issue',
            },
        )

        self.assertEqual(response.status_code, 302)

        company = self._public_company()
        self.assertEqual(company.support_access_reason, 'Debugging onboarding issue')
        self.assertEqual(company.support_access_updated_by, self.admin_user.username)
        self.assertIsNotNone(company.support_access_until)
        self.assertGreater(company.support_access_until, before)

        self.it_support_user.refresh_from_db()
        self.it_support_profile.refresh_from_db()
        self.assertTrue(self.it_support_user.is_active)
        self.assertTrue(self.it_support_profile.force_password_reset)
        mock_save.assert_called_once()

    @patch('apps.Employee.admin_views.CustomPasswordResetForm.save')
    def test_revoke_support_access_clears_window_and_deactivates_it_support(self, _mock_save):
        with schema_context('public'):
            company = Company.objects_with_deleted.get(schema_name=self.get_test_schema_name())
            company.support_access_mode = Company.SupportAccessMode.ON_REQUEST
            company.support_access_until = timezone.now() + timedelta(hours=12)
            company.save(update_fields=['support_access_mode', 'support_access_until'])

        self.it_support_user.is_active = True
        self.it_support_user.save(update_fields=['is_active'])

        response = self.client.post(
            self.url,
            {
                'revoke_support_access': '1',
                'support_access_reason': 'Issue resolved',
            },
        )

        self.assertEqual(response.status_code, 302)

        company = self._public_company()
        self.assertIsNone(company.support_access_until)
        self.assertEqual(company.support_access_reason, 'Issue resolved')
        self.assertEqual(company.support_access_updated_by, self.admin_user.username)

        self.it_support_user.refresh_from_db()
        self.assertFalse(self.it_support_user.is_active)

    def test_revoke_support_access_blocked_when_mode_is_always_allowed(self):
        with schema_context('public'):
            company = Company.objects_with_deleted.get(schema_name=self.get_test_schema_name())
            company.support_access_mode = Company.SupportAccessMode.ALWAYS
            company.support_access_until = timezone.now() + timedelta(hours=12)
            company.save(update_fields=['support_access_mode', 'support_access_until'])

        self.it_support_user.is_active = True
        self.it_support_user.save(update_fields=['is_active'])

        response = self.client.post(
            self.url,
            {
                'revoke_support_access': '1',
                'support_access_reason': 'Should be blocked in always mode',
            },
            follow=True,
        )

        messages = [message.message for message in get_messages(response.wsgi_request)]
        self.assertIn('Change support policy from Always Allowed before revoking support access.', messages)

        company = self._public_company()
        self.assertEqual(company.support_access_mode, Company.SupportAccessMode.ALWAYS)
        self.assertIsNotNone(company.support_access_until)

        self.it_support_user.refresh_from_db()
        self.assertTrue(self.it_support_user.is_active)
