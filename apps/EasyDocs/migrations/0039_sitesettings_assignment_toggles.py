from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('easydocs', '0038_backfill_process_assignments'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='allow_document_assigning',
            field=models.BooleanField(default=False, help_text='If enabled, document assign/accept workflows are available'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='allow_task_assigning',
            field=models.BooleanField(default=False, help_text='If enabled, task and process assignment workflows are available'),
        ),
    ]
