from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0007_company_support_privacy_mode'),
    ]

    operations = [
        migrations.AlterField(
            model_name='company',
            name='support_access_mode',
            field=models.CharField(
                choices=[('always', 'Always Allowed'), ('on_request', 'On Request Only'), ('disabled', 'Disabled')],
                default='always',
                help_text='Controls whether vendor IT Support can access this tenant by default or only on request.',
                max_length=20,
            ),
        ),
    ]
