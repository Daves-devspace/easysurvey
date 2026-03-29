from django.db import migrations, models


def backfill_bootstrap_it_identity(apps, schema_editor):
    Company = apps.get_model('tenants', 'Company')

    for company in Company.objects.all():
        changed = []
        if not company.bootstrap_it_email and company.admin_email:
            company.bootstrap_it_email = company.admin_email
            changed.append('bootstrap_it_email')
        if not company.bootstrap_it_name and company.admin_name:
            company.bootstrap_it_name = company.admin_name
            changed.append('bootstrap_it_name')
        if changed:
            company.save(update_fields=changed)


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0005_single_domain_per_tenant'),
    ]

    operations = [
        migrations.AddField(
            model_name='company',
            name='bootstrap_it_email',
            field=models.EmailField(blank=True, help_text='Mandatory tenant IT-support bootstrap email used for first login setup.', max_length=254),
        ),
        migrations.AddField(
            model_name='company',
            name='bootstrap_it_name',
            field=models.CharField(blank=True, help_text='Display name of the initial tenant IT-support user.', max_length=255),
        ),
        migrations.RunPython(backfill_bootstrap_it_identity, migrations.RunPython.noop),
    ]
