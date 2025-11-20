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

            # If dry_run, don't save — just print
            if dry_run:
                self.stdout.write(f"[DRY] Payment id={p.pk}: inst_cost_snapshot -> {new_inst_cost}, overridden_total_snapshot -> {new_overridden_total}")
            else:
                # only update if different (avoid unnecessary writes)
                changed = False
                if p.institution_cost_snapshot != new_inst_cost:
                    p.institution_cost_snapshot = new_inst_cost
                    changed = True
                if p.overridden_total_snapshot != new_overridden_total:
                    p.overridden_total_snapshot = new_overridden_total
                    changed = True
                if changed:
                    # save only the fields we updated
                    p.save(update_fields=['institution_cost_snapshot', 'overridden_total_snapshot'])
            # break if limit reached
            if limit and processed >= limit:
                break

        self.stdout.write(self.style.SUCCESS(f"Processed {processed} payments (dry_run={dry_run})"))

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
        for s in subs_qs.iterator():
            processed_subs += 1
            sub_template = getattr(s, 'sub_service', None)
            if sub_template is None:
                self.stdout.write(self.style.WARNING(f"ClientSubService id={s.pk} has no sub_service, skipping"))
                continue

            new_inst_cost = sub_template.price if sub_template.price is not None else Decimal('0.00')
            new_overridden_snapshot = s.overridden_price if s.overridden_price is not None else None

            if dry_run:
                self.stdout.write(f"[DRY] ClientSubService id={s.pk}: inst_cost_snapshot -> {new_inst_cost}, overridden_price_snapshot -> {new_overridden_snapshot}")
            else:
                changed = False
                if s.institution_cost_snapshot != new_inst_cost:
                    s.institution_cost_snapshot = new_inst_cost
                    changed = True
                if s.overridden_price_snapshot != new_overridden_snapshot:
                    s.overridden_price_snapshot = new_overridden_snapshot
                    changed = True
                if changed:
                    s.save(update_fields=['institution_cost_snapshot', 'overridden_price_snapshot'])

        self.stdout.write(self.style.SUCCESS(f"Processed {processed_subs} client subservices (dry_run={dry_run})"))
