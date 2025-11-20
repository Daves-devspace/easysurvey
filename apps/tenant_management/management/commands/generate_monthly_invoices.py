from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from apps.tenant_management.models import Lease, Invoice
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Generate monthly invoices for active leases'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be created without persisting'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        today = timezone.now().date()

        # Fetch active leases whose start_date is on or before today
        active_leases = Lease.objects.filter(
            is_active=True,
            start_date__lte=today
        ).select_related('unit__property', 'tenant')

        created_count = 0
        # Iterate each lease to ensure one invoice per calendar month
        for lease in active_leases:
            # Skip if an invoice already exists for the current month
            exists = Invoice.objects.filter(
                lease=lease,
                invoice_date__year=today.year,
                invoice_date__month=today.month
            ).exists()
            if exists:
                self.stdout.write(f"[SKIP] Invoice already exists for {lease}")
                continue

            # Determine invoice details
            invoice_date = today
            due_date = today + timedelta(days=30)
            rent = lease.unit.rent_amount

            # Dry-run: just print
            if dry_run:
                self.stdout.write(
                    f"[DRY-RUN] Would create invoice for {lease.tenant.full_name} "
                    f"(Unit {lease.unit.unit_number}), amount {rent}"
                )
            else:
                invoice = Invoice.objects.create(
                    lease=lease,
                    invoice_date=invoice_date,
                    due_date=due_date,
                    rent_amount=rent,
                    water_amount=0.0,  # to be calculated later
                    other_charges=0.0,
                    total_amount=rent,
                    is_paid=False,
                    auto_generated=True
                )
                self.stdout.write(f"[CREATE] Invoice {invoice.id} for {lease}")

            created_count += 1

        # Summary
        if dry_run:
            self.stdout.write(self.style.WARNING(f"Dry-run: {created_count} invoices would be created."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Success: {created_count} invoices created."))
        logger.info(f"Invoice generation run: {'dry-run' if dry_run else 'live'}; count={created_count}")
