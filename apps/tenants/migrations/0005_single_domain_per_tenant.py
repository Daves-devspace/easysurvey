from django.db import migrations, models
from django.db.models import Count


def collapse_domains_to_single_row(apps, schema_editor):
    Domain = apps.get_model('tenants', 'Domain')

    duplicate_tenants = (
        Domain.objects.values('tenant_id')
        .annotate(domain_count=Count('id'))
        .filter(domain_count__gt=1)
    )

    for item in duplicate_tenants:
        tenant_id = item['tenant_id']
        domains = list(
            Domain.objects.filter(tenant_id=tenant_id)
            .order_by('-is_primary', 'created_on', 'id')
        )
        canonical = domains[0]
        if not canonical.is_primary:
            canonical.is_primary = True
            canonical.save(update_fields=['is_primary'])

        duplicate_ids = [domain.id for domain in domains[1:]]
        if duplicate_ids:
            Domain.objects.filter(id__in=duplicate_ids).delete()

    Domain.objects.filter(is_primary=False).update(is_primary=True)


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0004_soft_delete_fields'),
    ]

    operations = [
        migrations.RunPython(collapse_domains_to_single_row, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='domain',
            constraint=models.UniqueConstraint(fields=('tenant',), name='unique_domain_per_tenant'),
        ),
    ]
