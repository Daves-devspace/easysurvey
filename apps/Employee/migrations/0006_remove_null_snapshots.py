# apps/Employee/migrations/0006_remove_null_snapshots.py

from django.db import migrations

def delete_null_snapshots(apps, schema_editor):
    AllowanceSnapshot = apps.get_model('Employee', 'AllowanceSnapshot')
    DeductionSnapshot = apps.get_model('Employee', 'DeductionSnapshot')
    # Drop any snapshots whose template is already NULL
    AllowanceSnapshot.objects.filter(template__isnull=True).delete()
    DeductionSnapshot.objects.filter(template__isnull=True).delete()

class Migration(migrations.Migration):

    dependencies = [
        ('Employee', '0005_allowancesnapshot_end_date_and_more'),
    ]

    operations = [
        migrations.RunPython(delete_null_snapshots, reverse_code=migrations.RunPython.noop),
    ]
