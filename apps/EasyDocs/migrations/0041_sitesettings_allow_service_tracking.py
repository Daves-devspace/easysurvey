from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('easydocs', '0040_booking_unique_booking_client_service_datetime'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='allow_service_tracking',
            field=models.BooleanField(
                default=True,
                help_text='If enabled, expected duration and deadline tracking is available',
            ),
        ),
    ]
