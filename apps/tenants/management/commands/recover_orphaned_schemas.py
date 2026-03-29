# apps/tenants/management/commands/recover_orphaned_schemas.py
"""
Management command: recover_orphaned_schemas

Finds PostgreSQL schemas that exist in the database but have no corresponding
Company (tenant) record.  This happens when Company rows are deleted (via admin
or raw SQL) but the actual schema was not dropped (auto_drop_schema=False).

Usage:
    # Dry-run report (default — safe, no writes):
    python manage.py recover_orphaned_schemas

    # Restore orphaned schemas by creating Company + Domain stub records:
    python manage.py recover_orphaned_schemas --restore

    # Also restore previously soft-deleted tenants whose schema is still intact:
    python manage.py recover_orphaned_schemas --restore --include-archived

    # Limit which schemas to examine:
    python manage.py recover_orphaned_schemas --schema-prefix myprefix_
"""

from django.core.management.base import BaseCommand
from django.db import connection
from django.utils.text import slugify
from django.utils import timezone


_SYSTEM_SCHEMAS = frozenset({
    'public',
    'information_schema',
    'pg_catalog',
    'pg_toast',
    'pg_temp_1',
    'pg_toast_temp_1',
})


class Command(BaseCommand):
    help = (
        'Report and optionally recover PostgreSQL schemas that have no matching '
        'Company (tenant) record. Run with --restore to create stub Company records '
        'so orphaned schemas become accessible again.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--restore',
            action='store_true',
            default=False,
            help=(
                'Create a stub Company + Domain record for each orphaned schema so it '
                'becomes accessible. Without this flag the command is a dry-run.'
            ),
        )
        parser.add_argument(
            '--include-archived',
            action='store_true',
            default=False,
            help='Also list soft-deleted (archived) tenants whose schema still exists.',
        )
        parser.add_argument(
            '--domain-suffix',
            default='localhost',
            help=(
                'Domain suffix used when auto-creating stub Domain records during --restore. '
                'Defaults to "localhost". Example: --domain-suffix 127.0.0.1.sslip.io'
            ),
        )

    def handle(self, *args, **options):
        from apps.tenants.models import Company, Domain

        restore = options['restore']
        include_archived = options['include_archived']
        domain_suffix = options['domain_suffix'].strip().lstrip('.')

        # ── 1. Collect all PostgreSQL schemas ─────────────────────────────────
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT schema_name
                FROM information_schema.schemata
                WHERE schema_name NOT IN %s
                  AND schema_name NOT LIKE 'pg_%%'
                ORDER BY schema_name
                """,
                [tuple(_SYSTEM_SCHEMAS)],
            )
            db_schemas = {row[0] for row in cursor.fetchall()}

        # ── 2. Collect schema names that already have Company records ─────────
        known_schemas = set(
            Company.objects_with_deleted.values_list('schema_name', flat=True)
        )

        # ── 3. Orphaned schemas = in DB but no Company row ────────────────────
        orphaned = sorted(db_schemas - known_schemas)

        # ── 4. Archived tenants whose schema is still in DB ───────────────────
        archived_with_schema = []
        if include_archived:
            archived_with_schema = list(
                Company.objects_with_deleted
                .filter(deleted_at__isnull=False, schema_name__in=db_schemas)
                .order_by('name')
            )

        # ── 5. Report ─────────────────────────────────────────────────────────
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('═' * 60))
        self.stdout.write(self.style.SUCCESS('  recover_orphaned_schemas'))
        self.stdout.write(self.style.SUCCESS('═' * 60))
        self.stdout.write(f'  PostgreSQL schemas found:  {len(db_schemas)}')
        self.stdout.write(f'  Known Company records:     {len(known_schemas)}')
        self.stdout.write(f'  Orphaned schemas:          {len(orphaned)}')
        if include_archived:
            self.stdout.write(f'  Archived tenants (schema still intact): {len(archived_with_schema)}')
        self.stdout.write('')

        if not orphaned:
            self.stdout.write(self.style.SUCCESS('✓  No orphaned schemas found — all good!'))
        else:
            self.stdout.write(self.style.WARNING('Orphaned schemas (schema exists, no Company record):'))
            for schema in orphaned:
                table_count = self._count_tables(schema)
                self.stdout.write(f'  • {schema:<40}  {table_count} tables')
            self.stdout.write('')

        if include_archived and archived_with_schema:
            self.stdout.write(self.style.WARNING('Archived tenants (schema intact, Company soft-deleted):'))
            for company in archived_with_schema:
                self.stdout.write(
                    f'  • {company.name:<40}  schema={company.schema_name}  '
                    f'archived={company.deleted_at.strftime("%Y-%m-%d")}  by={company.deleted_by}'
                )
            self.stdout.write('')

        # ── 6. Restore mode ───────────────────────────────────────────────────
        if restore:
            if orphaned:
                self.stdout.write(self.style.WARNING('Creating stub Company + Domain records...'))
                created = 0
                for schema in orphaned:
                    try:
                        company = self._create_stub_company(schema, domain_suffix)
                        self.stdout.write(
                            self.style.SUCCESS(
                                f'  ✓  Created Company "{company.name}" (schema={schema})'
                                f'  domain={company.domains.filter(is_primary=True).first()}'
                            )
                        )
                        created += 1
                    except Exception as exc:
                        self.stdout.write(
                            self.style.ERROR(f'  ✗  Failed to restore {schema}: {exc}')
                        )
                self.stdout.write('')
                self.stdout.write(self.style.SUCCESS(f'Restored {created}/{len(orphaned)} orphaned schemas.'))
            else:
                self.stdout.write(self.style.SUCCESS('Nothing to restore.'))

            if include_archived and archived_with_schema:
                self.stdout.write('')
                self.stdout.write(self.style.WARNING('Restoring archived tenants...'))
                for company in archived_with_schema:
                    company.restore()
                    self.stdout.write(self.style.SUCCESS(f'  ✓  Restored "{company.name}"'))
        else:
            if orphaned or (include_archived and archived_with_schema):
                self.stdout.write(
                    self.style.WARNING(
                        'Dry-run: no changes made. '
                        'Add --restore to create stub Company records and recover access.'
                    )
                )

        self.stdout.write('')

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _count_tables(self, schema_name: str) -> int:
        """Return the number of tables inside the given schema."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.tables
                WHERE table_schema = %s
                  AND table_type = 'BASE TABLE'
                """,
                [schema_name],
            )
            return cursor.fetchone()[0]

    def _create_stub_company(self, schema_name: str, domain_suffix: str):
        """
        Create a minimal Company + primary Domain for an orphaned schema so that
        django-tenants can route requests to it again.

        The admin can edit the record later to fill in proper details.
        """
        from apps.tenants.models import Company, Domain

        # Derive a human-readable name from the schema name
        name = schema_name.replace('_', ' ').title()
        slug = slugify(name)[:100]

        # Ensure slug uniqueness
        base_slug = slug
        counter = 2
        while Company.objects_with_deleted.filter(slug=slug).exists():
            slug = f'{base_slug}-{counter}'
            counter += 1

        # Ensure name uniqueness
        base_name = name
        counter = 2
        while Company.objects_with_deleted.filter(name=name).exists():
            name = f'{base_name} ({counter})'
            counter += 1

        # Create the Company row — we do NOT want to auto-create the schema
        # (it already exists), so we temporarily set auto_create_schema = False
        # by bypassing Company.save()'s TenantMixin logic using a direct super().save().
        company = Company.__new__(Company)
        Company.__init__(
            company,
            name=name,
            slug=slug,
            schema_name=schema_name,
            admin_email='recovered@localhost',
            plan='starter',
            is_active=True,
            notes=(
                f'[RECOVERED] Auto-created by recover_orphaned_schemas on '
                f'{timezone.now().strftime("%Y-%m-%d %H:%M")}. '
                f'Original schema: {schema_name}. Please fill in real details.'
            ),
        )
        # Skip schema creation — schema already exists
        company.auto_create_schema = False
        company.save()

        # Create a stub primary domain
        domain = f'{slug}.{domain_suffix}'
        # Ensure domain uniqueness
        from apps.tenants.models import Domain as DomainModel
        base_domain = domain
        counter = 2
        while DomainModel.objects.filter(domain=domain).exists():
            domain = f'{slug}-{counter}.{domain_suffix}'
            counter += 1

        DomainModel.objects.create(
            domain=domain,
            tenant=company,
            is_primary=True,
        )

        return company
