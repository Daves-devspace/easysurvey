# apps/Employee/migrations/0007_make_template_notnull.py

from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):

    dependencies = [
        ('Employee', '0006_remove_null_snapshots'),
    ]

    operations = [
        migrations.AlterField(
            model_name='allowancesnapshot',
            name='template',
            field=models.ForeignKey(
                to='Employee.AllowanceTemplate',
                on_delete=django.db.models.deletion.CASCADE,
                null=False,
            ),
        ),
        migrations.AlterField(
            model_name='deductionsnapshot',
            name='template',
            field=models.ForeignKey(
                to='Employee.DeductionTemplate',
                on_delete=django.db.models.deletion.CASCADE,
                null=False,
            ),
        ),
    ]
