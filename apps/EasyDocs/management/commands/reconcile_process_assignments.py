from django.core.management.base import BaseCommand

from apps.EasyDocs.models import ClientService
from apps.EasyDocs.services.process_assignments import sync_service_assignment_to_process_assignments


class Command(BaseCommand):
    help = (
        "Reconcile process-level assignments against service-level assignment state. "
        "Useful after rollouts, manual DB edits, or partial failures."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report drift without writing any DB changes.",
        )
        parser.add_argument(
            "--client-service-id",
            type=int,
            default=0,
            help="Optional: reconcile only one ClientService id.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Optional: max number of ClientService rows to inspect (0 = all).",
        )
        parser.add_argument(
            "--only-assigned",
            action="store_true",
            help="Only reconcile rows that currently have assigned_employee set.",
        )

    @staticmethod
    def _expected_acceptance_status(client_service):
        return "accepted" if client_service.assignment_status == "accepted" else "pending"

    def _drift_for_client_service(self, client_service):
        """Dry-run drift detection for one ClientService."""
        open_steps = client_service.service_processes.exclude(status__in=("completed", "collected"))

        missing_assignments = 0
        stale_assignments = 0
        status_drift = 0
        expected_acceptance = self._expected_acceptance_status(client_service)
        target_assignee_id = client_service.assigned_employee_id

        for step in open_steps:
            active_rows = list(step.assignments.filter(is_active=True))
            active_assignee_ids = {row.assignee_id for row in active_rows}
            target_assignee_ids = {target_assignee_id} if target_assignee_id else set()

            missing_assignments += len(target_assignee_ids - active_assignee_ids)
            stale_assignments += len(active_assignee_ids - target_assignee_ids)

            if target_assignee_id and target_assignee_id in active_assignee_ids:
                for row in active_rows:
                    if row.assignee_id == target_assignee_id and row.acceptance_status != expected_acceptance:
                        status_drift += 1

        return {
            "open_steps": open_steps.count(),
            "missing_assignments": missing_assignments,
            "stale_assignments": stale_assignments,
            "status_drift": status_drift,
        }

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        client_service_id = options["client_service_id"]
        limit = options["limit"]
        only_assigned = options["only_assigned"]

        queryset = ClientService.objects.select_related("assigned_employee").order_by("id")

        if client_service_id:
            queryset = queryset.filter(id=client_service_id)

        if only_assigned:
            queryset = queryset.exclude(assigned_employee__isnull=True)

        if limit and limit > 0:
            queryset = queryset[:limit]

        inspected = 0
        open_steps_total = 0
        missing_assignments = 0
        stale_assignments = 0
        status_drift = 0

        created = 0
        updated = 0
        deactivated = 0

        for client_service in queryset.iterator(chunk_size=250):
            inspected += 1

            if dry_run:
                drift = self._drift_for_client_service(client_service)
                open_steps_total += drift["open_steps"]
                missing_assignments += drift["missing_assignments"]
                stale_assignments += drift["stale_assignments"]
                status_drift += drift["status_drift"]
                continue

            result = sync_service_assignment_to_process_assignments(
                client_service=client_service,
                assigned_employee=client_service.assigned_employee,
                assigned_by=None,
                reason="Reconciled from service-level assignment",
            )
            created += result.get("created", 0)
            updated += result.get("updated", 0)
            deactivated += result.get("deactivated", 0)

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "[DRY RUN] "
                    f"inspected_services={inspected}, "
                    f"open_steps={open_steps_total}, "
                    f"missing_assignments={missing_assignments}, "
                    f"stale_assignments={stale_assignments}, "
                    f"status_drift={status_drift}"
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                "[APPLY] "
                f"inspected_services={inspected}, "
                f"created={created}, updated={updated}, deactivated={deactivated}"
            )
        )
