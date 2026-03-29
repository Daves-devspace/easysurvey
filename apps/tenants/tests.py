from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.db import connection
from django.db.models.signals import post_save
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from django_tenants.utils import schema_context

from apps.Employee.signals import create_admin_profile
from apps.tenants.models import Company, Domain
from apps.tenants.support_access import support_access_is_enabled
from apps.tenants.views import TenantArchiveView, TenantRestoreView


class SupportAccessPolicyTests(SimpleTestCase):
    def test_company_support_access_enabled_when_mode_is_always(self):
        company = Company(
            name="Acme",
            slug="acme",
            schema_name="acme",
            admin_email="admin@example.com",
            support_access_mode=Company.SupportAccessMode.ALWAYS,
        )

        self.assertTrue(company.support_access_is_enabled)
        self.assertTrue(support_access_is_enabled(company))

    def test_company_support_access_enabled_for_future_temp_window(self):
        company = Company(
            name="Acme",
            slug="acme",
            schema_name="acme",
            admin_email="admin@example.com",
            support_access_mode=Company.SupportAccessMode.ON_REQUEST,
            support_access_until=timezone.now() + timedelta(hours=4),
        )

        self.assertTrue(company.support_access_is_enabled)
        self.assertTrue(support_access_is_enabled(company))

    def test_company_support_access_disabled_without_active_window(self):
        company = Company(
            name="Acme",
            slug="acme",
            schema_name="acme",
            admin_email="admin@example.com",
            support_access_mode=Company.SupportAccessMode.DISABLED,
            support_access_until=timezone.now() - timedelta(minutes=5),
        )

        self.assertFalse(company.support_access_is_enabled)
        self.assertFalse(support_access_is_enabled(company))


class TenantArchiveRestoreViewTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        post_save.disconnect(create_admin_profile, sender=get_user_model())

        with schema_context('public'):
            original_auto_create = Company.auto_create_schema
            Company.auto_create_schema = False
            try:
                public_company = Company.objects_with_deleted.filter(schema_name='public').first()
                if not public_company:
                    public_company = Company.objects_with_deleted.create(
                        name='Public Tenant',
                        slug='public-tenant',
                        schema_name='public',
                        admin_email='public@example.com',
                        is_active=True,
                    )

                Domain.objects.filter(domain='testserver').exclude(tenant=public_company).delete()
                Domain.objects.update_or_create(
                    tenant=public_company,
                    defaults={'domain': 'testserver', 'is_primary': True},
                )
            finally:
                Company.auto_create_schema = original_auto_create

    @classmethod
    def tearDownClass(cls):
        post_save.connect(create_admin_profile, sender=get_user_model())
        super().tearDownClass()

    def setUp(self):
        connection.set_schema_to_public()
        User = get_user_model()
        User.objects.bulk_create([
            User(
                username='platform_admin',
                email='platform_admin@example.com',
                password=make_password('StrongAdminPass123!'),
                is_staff=True,
                is_superuser=True,
                is_active=True,
            )
        ])
        self.superuser = User.objects.get(username='platform_admin')
        self.factory = RequestFactory()

    def _create_company_stub(self, *, name, slug, schema_name):
        original_auto_create = Company.auto_create_schema
        Company.auto_create_schema = False
        try:
            with schema_context('public'):
                return Company.objects_with_deleted.create(
                    name=name,
                    slug=slug,
                    schema_name=schema_name,
                    admin_email='admin@example.com',
                    is_active=True,
                )
        finally:
            Company.auto_create_schema = original_auto_create

    def _post_request(self, path, data=None):
        request = self.factory.post(path, data=data or {})
        request.user = self.superuser

        session_middleware = SessionMiddleware(lambda req: None)
        session_middleware.process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)
        return request

    def test_archive_tenant_soft_deletes_and_deactivates(self):
        company = self._create_company_stub(
            name='Archive Target',
            slug='archive-target',
            schema_name='archive_target',
        )

        request = self._post_request('/subscriptions/tenants/archive-target/archive/', {'reason': 'Non-payment'})
        response = TenantArchiveView.as_view()(request, slug=company.slug)

        self.assertEqual(response.status_code, 302)
        company.refresh_from_db()
        self.assertFalse(company.is_active)
        self.assertIsNotNone(company.deleted_at)
        self.assertEqual(company.deleted_by, self.superuser.username)
        self.assertEqual(company.deletion_reason, 'Non-payment')

    def test_restore_tenant_reactivates_and_clears_archive_fields(self):
        company = self._create_company_stub(
            name='Restore Target',
            slug='restore-target',
            schema_name='restore_target',
        )
        company.soft_delete(user=self.superuser, reason='Temporary hold')

        request = self._post_request('/subscriptions/tenants/restore-target/restore/')
        response = TenantRestoreView.as_view()(request, slug=company.slug)

        self.assertEqual(response.status_code, 302)
        company.refresh_from_db()
        self.assertTrue(company.is_active)
        self.assertIsNone(company.deleted_at)
        self.assertEqual(company.deleted_by, '')
        self.assertEqual(company.deletion_reason, '')

    def test_archive_public_tenant_is_blocked(self):
        with schema_context('public'):
            public_company = Company.objects_with_deleted.get(schema_name='public')

        request = self._post_request('/subscriptions/tenants/public-tenant/archive/')
        response = TenantArchiveView.as_view()(request, slug=public_company.slug)

        self.assertEqual(response.status_code, 302)
        public_company.refresh_from_db()
        self.assertIsNone(public_company.deleted_at)
        self.assertTrue(public_company.is_active)