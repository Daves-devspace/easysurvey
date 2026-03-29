from django.db import migrations


TABLE_NAME = 'Employee_employeeprofile'
COLUMN_NAME = 'force_password_reset'


def repair_all_employee_profile_tables(apps, schema_editor):
    connection = schema_editor.connection
    quote_name = connection.ops.quote_name

    current_schema = getattr(connection, 'schema_name', None)
    if current_schema != 'public':
        return

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_schema
            FROM information_schema.tables
            WHERE table_name = %s
            ORDER BY table_schema
            """,
            [TABLE_NAME],
        )
        schemas = [row[0] for row in cursor.fetchall()]

        for schema in schemas:
            cursor.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = %s
                  AND table_name = %s
                  AND column_name = %s
                """,
                [schema, TABLE_NAME, COLUMN_NAME],
            )
            if cursor.fetchone():
                continue

            cursor.execute(
                f"ALTER TABLE {quote_name(schema)}.{quote_name(TABLE_NAME)} "
                f"ADD COLUMN {quote_name(COLUMN_NAME)} boolean NOT NULL DEFAULT false"
            )


class Migration(migrations.Migration):

    dependencies = [
        ('Employee', '0005_repair_force_password_reset_column'),
    ]

    operations = [
        migrations.RunPython(repair_all_employee_profile_tables, migrations.RunPython.noop),
    ]
