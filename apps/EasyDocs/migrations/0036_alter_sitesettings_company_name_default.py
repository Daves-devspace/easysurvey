from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('easydocs', '0035_rename_expense_handled_by_to_recorded_by'),
    ]

    operations = [
        migrations.AlterField(
            model_name='sitesettings',
            name='company_name',
            field=models.CharField(default='Plotsync', max_length=200),
        ),
    ]
