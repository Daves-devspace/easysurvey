from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0008_default_support_access_always'),
    ]

    operations = [
        migrations.AlterField(
            model_name='company',
            name='max_users',
            field=models.IntegerField(
                blank=True,
                default=None,
                help_text='Maximum number of users allowed (null = unlimited)',
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name='company',
            name='max_clients',
            field=models.IntegerField(
                blank=True,
                default=None,
                help_text='Maximum number of clients (null = unlimited)',
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name='company',
            name='max_storage_gb',
            field=models.IntegerField(
                blank=True,
                default=None,
                help_text='Maximum storage in GB (null = unlimited)',
                null=True,
            ),
        ),
    ]
