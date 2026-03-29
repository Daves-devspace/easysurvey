from django.db import connection
from django.utils import timezone
from django_tenants.utils import schema_context

from apps.tenants.models import Company


def get_company_for_schema(schema_name=None):
    schema_name = schema_name or connection.schema_name
    with schema_context('public'):
        return Company.objects_with_deleted.filter(schema_name=schema_name).first()


def support_access_is_enabled(company):
    if not company:
        return False
    if company.support_access_mode == Company.SupportAccessMode.ALWAYS:
        return True
    return bool(company.support_access_until and company.support_access_until > timezone.now())
