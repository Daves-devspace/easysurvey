from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Employee', '0003_alter_employeeprofile_role'),
    ]

    operations = [
        migrations.AddField(
            model_name='employeeprofile',
            name='force_password_reset',
            field=models.BooleanField(default=False, help_text='If True, user must reset password before next successful login.'),
        ),
    ]
