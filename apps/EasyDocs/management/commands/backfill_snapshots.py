# management/commands/backfill_snapshots.py
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from decimal import Decimal
from apps.EasyDocs.models import Payment, ClientSubService
from django.db.models import Q

CHUNK = 500


class Command(BaseCommand):
    help = "Backfill required snapshot fields for payments and client subservices"

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Do not save changes, just report')
        parser.add_argument('--force-zero', action='store_true', help='Treat 0.00 as missing and backfill those too')
        parser.add_argument('--limit', type=int, default=0, help='Optional limit to number of rows processed (0 = all)')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        force_zero = options['force_zero']
        limit = options['limit'] or None

        # --- Payments ---
        # filter payments where snapshot is NULL; optionally include zeros
        if force_zero:
            payments_qs = Payment.objects.filter(
                Q(institution_cost_snapshot__isnull=True) | Q(institution_cost_snapshot=Decimal('0.00'))
            )
        else:
            payments_qs = Payment.objects.filter(institution_cost_snapshot__isnull=True)

        total_payments = payments_qs.count()
        if limit:
            total_payments = min(total_payments, limit)

        self.stdout.write(f"Payments found to process: {total_payments} (force_zero={force_zero}, dry_run={dry_run})")

        processed = 0
        updated_count = 0
        # iterate in chunks to avoid memory issues
        qs_iter = payments_qs.iterator() if limit is None else payments_qs[:limit].iterator()

        for p in qs_iter:
            processed += 1
            svc = getattr(p, 'client_service', None)
            if svc is None:
                self.stdout.write(self.style.WARNING(f"Payment id={p.pk} has no client_service, skipping"))
                continue
            # worker-safe: service may be missing
            service = getattr(svc, 'service', None)
            if service is None:
                self.stdout.write(self.style.WARNING(f"Payment id={p.pk} client_service has no service, skipping"))
                continue

            new_inst_cost = service.total_price if service.total_price is not None else Decimal('0.00')
            # prefer overridden_total if present on client_service, otherwise full_total_price
            new_overridden_total = svc.overridden_total_price if svc.overridden_total_price is not None else svc.full_total_price

            # Check if update is needed
            needs_update = (
                p.institution_cost_snapshot != new_inst_cost or 
                p.overridden_total_snapshot != new_overridden_total
            )

            # If dry_run, don't save — just print
            if dry_run:
                if needs_update:
                    self.stdout.write(
                        f"[DRY] Payment id={p.pk}: "
                        f"inst_cost_snapshot {p.institution_cost_snapshot} -> {new_inst_cost}, "
                        f"overridden_total_snapshot {p.overridden_total_snapshot} -> {new_overridden_total}"
                    )
            else:
                if needs_update:
                    # Use QuerySet.update() to bypass model validation (clean() method)
                    # This directly updates the database without triggering save() or clean()
                    Payment.objects.filter(pk=p.pk).update(
                        institution_cost_snapshot=new_inst_cost,
                        overridden_total_snapshot=new_overridden_total
                    )
                    updated_count += 1
                    if updated_count % 100 == 0:
                        self.stdout.write(f"Updated {updated_count} payments so far...")
            
            # break if limit reached
            if limit and processed >= limit:
                break

        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {processed} payments, updated {updated_count} (dry_run={dry_run})"
            )
        )

        # --- ClientSubService ---
        if force_zero:
            subs_qs = ClientSubService.objects.filter(
                Q(institution_cost_snapshot__isnull=True) | Q(institution_cost_snapshot=Decimal('0.00'))
            )
        else:
            subs_qs = ClientSubService.objects.filter(institution_cost_snapshot__isnull=True)

        total_subs = subs_qs.count()
        self.stdout.write(f"ClientSubService found to process: {total_subs} (force_zero={force_zero}, dry_run={dry_run})")

        processed_subs = 0
        updated_subs = 0
        for s in subs_qs.iterator():
            processed_subs += 1
            sub_template = getattr(s, 'sub_service', None)
            if sub_template is None:
                self.stdout.write(self.style.WARNING(f"ClientSubService id={s.pk} has no sub_service, skipping"))
                continue

            new_inst_cost = sub_template.price if sub_template.price is not None else Decimal('0.00')
            new_overridden_snapshot = s.overridden_price if s.overridden_price is not None else None

            needs_update = (
                s.institution_cost_snapshot != new_inst_cost or 
                s.overridden_price_snapshot != new_overridden_snapshot
            )

            if dry_run:
                if needs_update:
                    self.stdout.write(
                        f"[DRY] ClientSubService id={s.pk}: "
                        f"inst_cost_snapshot -> {new_inst_cost}, "
                        f"overridden_price_snapshot -> {new_overridden_snapshot}"
                    )
            else:
                if needs_update:
                    # Use QuerySet.update() to bypass any model validation
                    ClientSubService.objects.filter(pk=s.pk).update(
                        institution_cost_snapshot=new_inst_cost,
                        overridden_price_snapshot=new_overridden_snapshot
                    )
                    updated_subs += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {processed_subs} client subservices, updated {updated_subs} (dry_run={dry_run})"
            )
        )