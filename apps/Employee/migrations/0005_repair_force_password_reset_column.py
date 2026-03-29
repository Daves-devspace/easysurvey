from django.db import migrations


TABLE_NAME = 'Employee_employeeprofile'
COLUMN_NAME = 'force_password_reset'


def ensure_force_password_reset_column(apps, schema_editor):
    connection = schema_editor.connection
    quote_name = connection.ops.quote_name

    with connection.cursor() as cursor:
        existing_tables = connection.introspection.table_names(cursor)
        if TABLE_NAME not in existing_tables:
            return

        columns = {
            column.name for column in connection.introspection.get_table_description(cursor, TABLE_NAME)
        }
        if COLUMN_NAME in columns:
            return

        cursor.execute(
            f"ALTER TABLE {quote_name(TABLE_NAME)} ADD COLUMN {quote_name(COLUMN_NAME)} boolean NOT NULL DEFAULT false"
        )


class Migration(migrations.Migration):

    dependencies = [
        ('Employee', '0004_employeeprofile_force_password_reset'),
    ]

    operations = [
        migrations.RunPython(ensure_force_password_reset_column, migrations.RunPython.noop),
    ]
