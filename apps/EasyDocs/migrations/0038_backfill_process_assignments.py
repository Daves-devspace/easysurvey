from django.db import migrations


def _map_acceptance_status(service_assignment_status):
    if service_assignment_status == 'accepted':
        return 'accepted'
    if service_assignment_status in ('pending_acceptance', 'reassigned'):
        return 'pending'
    return 'pending'


def _map_completion_status(process_status):
    return 'completed' if process_status in ('completed', 'collected') else 'pending'


def forward_backfill_process_assignments(apps, schema_editor):
    ClientServiceProcess = apps.get_model('easydocs', 'ClientServiceProcess')
    ClientServiceProcessAssignment = apps.get_model('easydocs', 'ClientServiceProcessAssignment')

    db_alias = schema_editor.connection.alias
    batch_size = 1000
    pending = []

    queryset = (
        ClientServiceProcess.objects.using(db_alias)
        .select_related('client_service')
        .exclude(client_service__assigned_employee__isnull=True)
        .order_by('id')
    )

    for step in queryset.iterator(chunk_size=batch_size):
        client_service = step.client_service
        assignee_id = client_service.assigned_employee_id
        if not assignee_id:
            continue

        pending.append(
            ClientServiceProcessAssignment(
                client_service_process_id=step.id,
                assignee_id=assignee_id,
                assigned_by_id=None,
                is_active=True,
                acceptance_status=_map_acceptance_status(client_service.assignment_status),
                completion_status=_map_completion_status(step.status),
                completed_at=step.completed_at if step.status in ('completed', 'collected') else None,
            )
        )

        if len(pending) >= batch_size:
            ClientServiceProcessAssignment.objects.using(db_alias).bulk_create(
                pending,
                batch_size=batch_size,
                ignore_conflicts=True,
            )
            pending = []

    if pending:
        ClientServiceProcessAssignment.objects.using(db_alias).bulk_create(
            pending,
            batch_size=batch_size,
            ignore_conflicts=True,
        )


def reverse_noop(apps, schema_editor):
    """Intentionally no-op to preserve assignment history once created."""
    return


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('easydocs', '0037_clientserviceprocessassignment_and_more'),
    ]

    operations = [
        migrations.RunPython(
            forward_backfill_process_assignments,
            reverse_noop,
        ),
    ]
