from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.urls import reverse
from django_tenants.test.cases import TenantTestCase
from django_tenants.utils import schema_context

from apps.Employee.models import EmployeeProfile
from apps.tenants.models import Company, Domain


User = get_user_model()


class SupportPrivacyLoginTests(TenantTestCase):
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
        return 'support_privacy_test'

    @classmethod
    def get_test_tenant_domain(cls):
        return 'support-privacy.test.com'

    @classmethod
    def setup_tenant(cls, tenant):
        tenant.name = f'{cls.get_test_schema_name()} Tenant'
        tenant.slug = f'{cls.get_test_schema_name()}-tenant'
        tenant.admin_email = 'admin@example.com'
        tenant.bootstrap_it_email = 'support@example.com'
        tenant.bootstrap_it_name = 'IT Support'
        tenant.support_access_mode = Company.SupportAccessMode.ALWAYS

    def setUp(self):
        self.password = 'StrongPass123!'
        self.client.defaults['HTTP_HOST'] = self.get_test_tenant_domain()
        self.it_user = User.objects.create_user(
            username='itsupport',
            email='support@example.com',
            password=self.password,
        )
        self.it_profile = EmployeeProfile.objects.create(
            user=self.it_user,
            role=EmployeeProfile.RoleChoices.IT_SUPPORT,
        )

    @patch('apps.EasyDocs.auth_views.support_access_is_enabled', return_value=False)
    @patch('apps.EasyDocs.auth_views.get_company_for_schema')
    def test_it_support_login_blocked_when_support_access_disabled(self, mock_company, _mock_enabled):
        mock_company.return_value = object()

        response = self.client.post(
            reverse('login'),
            {'username': self.it_user.username, 'password': self.password},
            follow=True,
        )
        messages = [message.message for message in get_messages(response.wsgi_request)]

        self.assertIn(
            'IT Support access is currently disabled for this tenant. Ask a tenant admin to grant temporary support access.',
            messages,
        )
        self.assertNotIn('_auth_user_id', self.client.session)

    @patch('apps.EasyDocs.auth_views.support_access_is_enabled', return_value=True)
    @patch('apps.EasyDocs.auth_views.get_company_for_schema')
    def test_it_support_login_allowed_when_support_access_enabled(self, mock_company, _mock_enabled):
        mock_company.return_value = object()

        response = self.client.post(
            reverse('login'),
            {'username': self.it_user.username, 'password': self.password},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(str(self.client.session.get('_auth_user_id')), str(self.it_user.pk))

    @patch('apps.EasyDocs.auth_views.CustomPasswordResetForm.save')
    @patch('apps.EasyDocs.auth_views.support_access_is_enabled', return_value=True)
    @patch('apps.EasyDocs.auth_views.get_company_for_schema')
    def test_force_password_reset_blocks_login_and_sends_reset(self, mock_company, _mock_enabled, mock_save):
        mock_company.return_value = object()
        self.it_profile.force_password_reset = True
        self.it_profile.save(update_fields=['force_password_reset'])

        response = self.client.post(
            reverse('login'),
            {'username': self.it_user.username, 'password': self.password},
            follow=True,
        )

        mock_save.assert_called_once()
        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertNotIn('_auth_user_id', self.client.session)