# apps/EasyDocs/management/commands/normalize_payments.py
import os
import json
import csv
from decimal import Decimal
from datetime import datetime
from django.core.management.base import BaseCommand
from django.db import transaction
from django.conf import settings
from apps.EasyDocs.models import Payment

class Command(BaseCommand):
    help = "Normalize and backfill missing Payment snapshots safely."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate changes without saving them.",
        )
        parser.add_argument(
            "--format",
            choices=["json", "csv"],
            default="json",
            help="Output log format (json or csv).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        output_format = options["format"]

        updated, skipped, errors = 0, 0, 0
        log_entries = []

        log_dir = os.path.join(settings.BASE_DIR, "logs", "normalize_payments")
        os.makedirs(log_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = os.path.join(log_dir, f"normalize_{timestamp}.{output_format}")

        self.stdout.write(self.style.SUCCESS("🚀 Starting payment normalization..."))

        # Process each client_service group
        cs_ids = (
            Payment.objects.values_list("client_service_id", flat=True)
            .distinct()
            .order_by("client_service_id")
        )

        for cs_id in cs_ids:
            payments = Payment.objects.filter(client_service_id=cs_id).order_by("payment_date", "id")
            if not payments.exists():
                continue

            client_service = payments.first().client_service
            service_price = getattr(client_service.service, "total_price", Decimal("0.00")) or Decimal("0.00")
            overridden_total = getattr(client_service, "overridden_total_price", service_price)

            # Determine baseline snapshot
            first_payment = payments.first()
            if not first_payment.institution_cost_snapshot or first_payment.institution_cost_snapshot == 0:
                base_institution = service_price
                base_total = overridden_total
            else:
                base_institution = first_payment.institution_cost_snapshot
                base_total = first_payment.overridden_total_snapshot

            for p in payments:
                try:
                    entry = {
                        "payment_id": p.id,
                        "client_service_id": cs_id,
                        "payment_date": p.payment_date.isoformat(),
                        "old_institution": str(p.institution_cost_snapshot),
                        "old_overridden": str(p.overridden_total_snapshot),
                        "new_institution": str(base_institution),
                        "new_overridden": str(base_total),
                        "action": None,
                    }

                    # Detect missing or invalid snapshot values
                    if not p.institution_cost_snapshot or float(p.institution_cost_snapshot) == 0.0:
                        entry["action"] = "updated"
                        if not dry_run:
                            p.institution_cost_snapshot = base_institution
                            p.overridden_total_snapshot = base_total
                            p.save(update_fields=["institution_cost_snapshot", "overridden_total_snapshot"])
                        updated += 1
                    else:
                        entry["action"] = "skipped"
                        skipped += 1

                except Exception as e:
                    entry["action"] = f"error: {str(e)}"
                    errors += 1

                log_entries.append(entry)

        # Write logs
        if output_format == "json":
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(log_entries, f, indent=2, ensure_ascii=False)
        else:
            with open(log_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=log_entries[0].keys())
                writer.writeheader()
                writer.writerows(log_entries)

        self.stdout.write(self.style.SUCCESS(
            f"✅ Done. Updated: {updated}, Skipped: {skipped}, Errors: {errors}"
        ))
        self.stdout.write(self.style.SUCCESS(f"📄 Log saved to: {log_path}"))
