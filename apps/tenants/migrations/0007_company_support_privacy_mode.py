from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0006_company_bootstrap_it_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='company',
            name='support_access_mode',
            field=models.CharField(choices=[('always', 'Always Allowed'), ('on_request', 'On Request Only'), ('disabled', 'Disabled')], default='on_request', help_text='Controls whether vendor IT Support can access this tenant by default or only on request.', max_length=20),
        ),
        migrations.AddField(
            model_name='company',
            name='support_access_reason',
            field=models.TextField(blank=True, help_text='Most recent reason provided when support access policy or window was changed.'),
        ),
        migrations.AddField(
            model_name='company',
            name='support_access_until',
            field=models.DateTimeField(blank=True, help_text='If set in the future, tenant IT Support access is temporarily granted until this time.', null=True),
        ),
        migrations.AddField(
            model_name='company',
            name='support_access_updated_by',
            field=models.CharField(blank=True, help_text='Username that last changed the support access policy/window.', max_length=255),
        ),
    ]
