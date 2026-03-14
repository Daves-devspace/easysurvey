from django.core.management.base import BaseCommand

from apps.EasyDocs.models import (
    ClientServiceProcess,
    ClientServiceProcessAssignment,
)


class Command(BaseCommand):
    help = "Backfill process-level assignments from existing service-level assignments"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be created without writing to DB",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Optional max number of process rows to inspect (0 = all)",
        )
        parser.add_argument(
            "--include-completed",
            action="store_true",
            help="Include completed/collected process rows in backfill",
        )

    @staticmethod
    def map_acceptance_status(service_assignment_status):
        if service_assignment_status == "accepted":
            return "accepted"
        if service_assignment_status in ("pending_acceptance", "reassigned"):
            return "pending"
        return "pending"

    @staticmethod
    def map_completion_status(process_status):
        return "completed" if process_status in ("completed", "collected") else "pending"

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]
        include_completed = options["include_completed"]

        queryset = (
            ClientServiceProcess.objects
            .select_related("client_service")
            .exclude(client_service__assigned_employee__isnull=True)
            .order_by("id")
        )

        if not include_completed:
            queryset = queryset.exclude(status__in=("completed", "collected"))

        if limit and limit > 0:
            queryset = queryset[:limit]

        inspected = 0
        created = 0
        skipped_existing = 0

        for step in queryset.iterator(chunk_size=500):
            inspected += 1
            client_service = step.client_service
            assignee_id = client_service.assigned_employee_id

            if not assignee_id:
                continue

            defaults = {
                "assigned_by_id": None,
                "acceptance_status": self.map_acceptance_status(client_service.assignment_status),
                "completion_status": self.map_completion_status(step.status),
                "completed_at": step.completed_at if step.status in ("completed", "collected") else None,
            }

            exists = ClientServiceProcessAssignment.objects.filter(
                client_service_process_id=step.id,
                assignee_id=assignee_id,
                is_active=True,
            ).exists()

            if exists:
                skipped_existing += 1
                continue

            if dry_run:
                created += 1
                continue

            ClientServiceProcessAssignment.objects.create(
                client_service_process_id=step.id,
                assignee_id=assignee_id,
                is_active=True,
                **defaults,
            )
            created += 1

        mode = "DRY RUN" if dry_run else "APPLY"
        self.stdout.write(
            self.style.SUCCESS(
                f"[{mode}] inspected={inspected}, created={created}, skipped_existing={skipped_existing}"
            )
        )
