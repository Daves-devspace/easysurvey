"""
Management command to bootstrap the public (landing) tenant, optionally create a
demo tenant, and ensure a platform superadmin user exists.

Idempotent — safe to run on every container start.

Usage:
    python manage.py create_public_tenant
    python manage.py create_public_tenant --domain localhost --create-demo --demo-domain demo.localhost \
        --superadmin-username admin --superadmin-email admin@plotsync.com --superadmin-password changeme
"""
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import ProgrammingError

from apps.tenants.models import Company, Domain

User = get_user_model()


class Command(BaseCommand):
    help = "Bootstrap public tenant, optional demo tenant, and platform superadmin. Idempotent."

    def add_arguments(self, parser):
        site_domain = _strip_domain(os.environ.get("SITE_DOMAIN", "localhost"))
        demo_domain = _default_demo_domain(site_domain)

        parser.add_argument(
            "--domain",
            default=site_domain,
            help=f"Primary domain for the public tenant (default: {site_domain})",
        )
        parser.add_argument(
            "--extra-domains",
            nargs="*",
            default=[],
            metavar="DOMAIN",
            help="Deprecated. Single-domain mode is enforced; extra domains are ignored.",
        )
        parser.add_argument(
            "--name",
            default="PlotSync Public",
            help="Display name for the public tenant",
        )
        parser.add_argument(
            "--admin-email",
            default="admin@plotsync.com",
            help="Admin email for the public tenant",
        )
        # --- Demo tenant ---
        parser.add_argument(
            "--create-demo",
            action="store_true",
            default=False,
            help="Also create a demo company tenant",
        )
        parser.add_argument(
            "--demo-domain",
            default=demo_domain,
            help=f"Domain for the demo tenant (default: {demo_domain})",
        )
        parser.add_argument(
            "--demo-name",
            default="Demo Company",
            help="Display name for the demo tenant",
        )
        parser.add_argument(
            "--force-demo-domain-sync",
            action="store_true",
            default=False,
            help="Overwrite the existing demo tenant domain with --demo-domain if the tenant already exists.",
        )
        # --- Superadmin ---
        parser.add_argument("--superadmin-username", default=os.environ.get("SUPERADMIN_USERNAME", ""))
        parser.add_argument("--superadmin-email", default=os.environ.get("SUPERADMIN_EMAIL", ""))
        parser.add_argument("--superadmin-password", default=os.environ.get("SUPERADMIN_PASSWORD", ""))

    def handle(self, *args, **options):
        # ------------------------------------------------------------------ #
        # 1. Public tenant                                                     #
        # ------------------------------------------------------------------ #
        primary_domain = _strip_domain(options["domain"])
        if options["extra_domains"]:
            self.stdout.write(self.style.WARNING("Ignoring --extra-domains because single-domain mode is enforced."))

        tenant, created = _get_or_create_tenant(
            schema_name="public",
            name=options["name"],
            slug="public",
            admin_email=options["admin_email"],
        )
        self.stdout.write(
            self.style.SUCCESS(f"{'Created' if created else 'Exists'}: public tenant '{tenant.name}'")
        )
        _sync_single_domain(self, tenant, primary_domain)

        # ------------------------------------------------------------------ #
        # 2. Demo tenant (optional)                                           #
        # ------------------------------------------------------------------ #
        if options["create_demo"]:
            demo_tenant, demo_created = _get_or_create_tenant(
                schema_name="demo_company",
                name=options["demo_name"],
                slug="demo",
                admin_email=options["admin_email"],
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"{'Created' if demo_created else 'Exists'}: demo tenant '{demo_tenant.name}'"
                )
            )
            demo_domain = _strip_domain(options["demo_domain"])
            if demo_created or options["force_demo_domain_sync"] or not Domain.objects.filter(tenant=demo_tenant).exists():
                _sync_single_domain(self, demo_tenant, demo_domain)
            else:
                existing_primary = Domain.objects.filter(tenant=demo_tenant, is_primary=True).first()
                current_domain = existing_primary.domain if existing_primary else "(none)"
                self.stdout.write(
                    self.style.WARNING(
                        f"  Preserving existing demo domain: {current_domain}. "
                        f"Use --force-demo-domain-sync to replace it with {demo_domain}."
                    )
                )

        # ------------------------------------------------------------------ #
        # 3. Platform superadmin                                              #
        # ------------------------------------------------------------------ #
        username = options["superadmin_username"]
        email = options["superadmin_email"]
        password = options["superadmin_password"]

        if username and password:
            user, u_created = User.objects.get_or_create(
                username=username,
                defaults={"email": email, "is_staff": True, "is_superuser": True},
            )
            if u_created:
                user.set_password(password)
                user.save()
                self.stdout.write(self.style.SUCCESS(f"Created superadmin: {username}"))
            else:
                self.stdout.write(f"Superadmin already exists: {username}")
        else:
            self.stdout.write("Skipping superadmin creation (no --superadmin-username/password provided)")

        self.stdout.write(self.style.SUCCESS("Bootstrap complete."))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _strip_domain(value: str) -> str:
    """Strip http(s):// and trailing slashes/paths from a SITE_DOMAIN value."""
    for prefix in ("https://", "http://"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    # drop port (e.g. localhost:8080 → localhost)
    value = value.split(":")[0].split("/")[0]
    return value or "localhost"


def _default_demo_domain(site_domain: str) -> str:
    dev_base = _strip_domain(os.environ.get("TENANT_DEV_BASE_DOMAIN", ""))
    if dev_base and dev_base != "localhost":
        return f"demo.{dev_base}"
    return f"demo.{site_domain}"


def _get_or_create_tenant(schema_name, name, slug, admin_email):
    """Create a Company, skipping auto_create_schema for the public schema."""
    try:
        company = Company.objects.get(schema_name=schema_name)
        return company, False
    except Company.DoesNotExist:
        pass

    if schema_name == "public":
        # The public schema already exists in PostgreSQL — don't attempt CREATE SCHEMA
        Company.auto_create_schema = False

    try:
        company = Company(
            schema_name=schema_name,
            name=name,
            slug=slug,
            admin_email=admin_email,
            bootstrap_it_email=admin_email,
            bootstrap_it_name="Tenant IT Support",
            is_active=True,
        )
        company.save()
        return company, True
    except ProgrammingError:
        # Schema already exists (race or pre-existing)
        Company.auto_create_schema = True
        company = Company.objects.get(schema_name=schema_name)
        return company, False
    finally:
        # Always restore default for subsequent tenant creations
        Company.auto_create_schema = True


def _sync_single_domain(cmd, tenant, domain_str):
    conflict = Domain.objects.exclude(tenant=tenant).filter(domain=domain_str).select_related('tenant').first()
    if conflict:
        raise CommandError(
            f'Domain "{domain_str}" already belongs to tenant "{conflict.tenant.name}".'
        )

    existing_domains = list(Domain.objects.filter(tenant=tenant).order_by('-is_primary', 'created_on', 'pk'))
    canonical = next((item for item in existing_domains if item.domain == domain_str), None)

    if canonical is None and existing_domains:
        canonical = existing_domains[0]
        canonical.domain = domain_str

    if canonical is None:
        Domain.objects.create(domain=domain_str, tenant=tenant, is_primary=True)
        cmd.stdout.write(cmd.style.SUCCESS(f'  Added domain: {domain_str}'))
        return

    canonical.is_primary = True
    canonical.save()

    duplicates = [item.pk for item in existing_domains if item.pk != canonical.pk]
    if duplicates:
        Domain.objects.filter(pk__in=duplicates).delete()
        cmd.stdout.write(cmd.style.WARNING(f'  Removed {len(duplicates)} extra domain(s) for {tenant.name}'))

    if canonical.domain == domain_str:
        cmd.stdout.write(cmd.style.SUCCESS(f'  Synced domain: {domain_str}'))
