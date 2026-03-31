import pytest
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.core import mail
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django_tenants.utils import schema_context
from django_tenants.test.client import TenantClient
from apps.tenants.models import Company, Domain
from django.contrib.auth.tokens import default_token_generator

@pytest.mark.django_db
def test_password_reset_only_valid_user():
    company = Company.objects.create(
        name="TestCo",
        slug="testco",
        schema_name="testco",
        paid_until="2099-01-01",
        admin_email="admin@testco.com"
    )
    Domain.objects.create(domain="testco.localhost", tenant=company, is_primary=True)
    with schema_context("testco"):
        User = get_user_model()
        user1 = User.objects.create_user(username='user1', email='user1@example.com', password='pass')
        user2 = User.objects.create_user(username='user2', email='user2@example.com', password='pass')
        user2_pk = user2.pk
        user2.delete()

        # Debug: print all users in the schema before password reset
        print("All users in schema before password reset:")
        for u in User.objects.all():
            print(f"User: pk={u.pk}, username={u.username}, email={u.email}, is_active={u.is_active}")

        tenant_client = TenantClient(company)

        # Print the resolved password reset URL
        print("Password reset URL:", reverse('password_reset'))

        # Patch the site_settings context processor to avoid DB access
        with patch("apps.EasyDocs.context_processors.site_settings") as mock_site_settings:
            mock_site_settings.return_value = {
                'site_settings': None,
                'logo_ts': None,
                'logo_url': '',
                'company_name': 'TestCo',
            }

            # Attempt password reset for deleted user
            response = tenant_client.post(
                reverse('password_reset'),
                {'email': 'user2@example.com'}
            )
            # Should not send email
            assert len(mail.outbox) == 0

            # Attempt password reset for valid user
            response = tenant_client.post(
                reverse('password_reset'),
                {'email': 'user1@example.com'}
            )
            # Should send email
            print("Password reset response status:", response.status_code)
            print("Password reset response content:", response.content.decode())
            assert len(mail.outbox) == 1
            assert 'user1@example.com' in mail.outbox[0].to

@pytest.mark.django_db
def test_password_reset_confirm_valid_token():
    company = Company.objects.create(
        name="TestCo",
        slug="testco",
        schema_name="testco",
        paid_until="2099-01-01",
        admin_email="admin@testco.com"
    )
    domain = Domain.objects.create(domain="testco.localhost", tenant=company, is_primary=True)
    print(f"Created domain: {domain.domain} for tenant: {company.schema_name}")
    with schema_context("testco"):
        User = get_user_model()
        user = User.objects.create_user(username='user1', email='user1@example.com', password='pass')
        tenant_client = TenantClient(company)
        uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        url = reverse('password_reset_confirm', kwargs={'uidb64': uidb64, 'token': token})
        print(f"Testing URL: {url}")
        print(f"All domains: {[d.domain for d in Domain.objects.all()]}")
        print(f"Current schema: {company.schema_name}")
        response = tenant_client.get(url, HTTP_HOST='testco.localhost', follow=True)
        print(f"Response status: {response.status_code}")
        print(f"Response content: {response.content[:200]}")
        assert response.status_code == 200
        assert b"new password" in response.content.lower() or b"reset" in response.content.lower()

@pytest.mark.django_db
def test_password_reset_confirm_invalid_token():
    company = Company.objects.create(
        name="TestCo",
        slug="testco",
        schema_name="testco",
        paid_until="2099-01-01",
        admin_email="admin@testco.com"
    )
    domain = Domain.objects.create(domain="testco.localhost", tenant=company, is_primary=True)
    print(f"Created domain: {domain.domain} for tenant: {company.schema_name}")
    with schema_context("testco"):
        User = get_user_model()
        user = User.objects.create_user(username='user1', email='user1@example.com', password='pass')
        tenant_client = TenantClient(company)
        uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
        token = "invalid-token"
        url = reverse('password_reset_confirm', kwargs={'uidb64': uidb64, 'token': token})
        print(f"Testing URL: {url}")
        print(f"All domains: {[d.domain for d in Domain.objects.all()]}")
        print(f"Current schema: {company.schema_name}")
        response = tenant_client.get(url, HTTP_HOST='testco.localhost')
        print(f"Response status: {response.status_code}")
        print(f"Response content: {response.content[:200]}")
        assert response.status_code == 200
        assert b"invalid" in response.content.lower() or b"error" in response.content.lower() or b"reset" in response.content.lower()

@pytest.mark.django_db
def test_password_reset_confirm_nonexistent_user():
    company = Company.objects.create(
        name="TestCo",
        slug="testco",
        schema_name="testco",
        paid_until="2099-01-01",
        admin_email="admin@testco.com"
    )
    domain = Domain.objects.create(domain="testco.localhost", tenant=company, is_primary=True)
    print(f"Created domain: {domain.domain} for tenant: {company.schema_name}")
    with schema_context("testco"):
        User = get_user_model()
        tenant_client = TenantClient(company)
        uidb64 = urlsafe_base64_encode(force_bytes(9999))  # Nonexistent pk
        user = User.objects.create_user(username='user1', email='user1@example.com', password='pass')
        token = default_token_generator.make_token(user)
        url = reverse('password_reset_confirm', kwargs={'uidb64': uidb64, 'token': token})
        print(f"Testing URL: {url}")
        print(f"All domains: {[d.domain for d in Domain.objects.all()]}")
        print(f"Current schema: {company.schema_name}")
        response = tenant_client.get(url, HTTP_HOST='testco.localhost')
        print(f"Response status: {response.status_code}")
        print(f"Response content: {response.content[:200]}")
        assert response.status_code == 200
        assert b"invalid" in response.content.lower() or b"error" in response.content.lower() or b"reset" in response.content.lower()

@pytest.mark.django_db
def test_password_reset_confirm_inactive_user():
    company = Company.objects.create(
        name="TestCo",
        slug="testco",
        schema_name="testco",
        paid_until="2099-01-01",
        admin_email="admin@testco.com"
    )
    domain = Domain.objects.create(domain="testco.localhost", tenant=company, is_primary=True)
    print(f"Created domain: {domain.domain} for tenant: {company.schema_name}")
    with schema_context("testco"):
        User = get_user_model()
        user = User.objects.create_user(username='user1', email='user1@example.com', password='pass', is_active=False)
        tenant_client = TenantClient(company)
        uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        url = reverse('password_reset_confirm', kwargs={'uidb64': uidb64, 'token': token})
        print(f"Testing URL: {url}")
        print(f"All domains: {[d.domain for d in Domain.objects.all()]}")
        print(f"Current schema: {company.schema_name}")
        response = tenant_client.get(url, HTTP_HOST='testco.localhost', follow=True)
        print(f"Response status: {response.status_code}")
        print(f"Response content: {response.content[:200]}")
        assert response.status_code == 200
        assert b"inactive" in response.content.lower() or b"invalid" in response.content.lower() or b"error" in response.content.lower() or b"reset" in response.content.lower()