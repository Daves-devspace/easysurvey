"""
Management command to bootstrap the public (landing) tenant, optionally create a
demo tenant, and ensure a platform superadmin user exists.

Idempotent — safe to run on every container start.

Usage:
    python manage.py create_public_tenant
    python manage.py create_public_tenant --domain localhost --create-demo --demo-domain demo.localhost \\
        --superadmin-username admin --superadmin-email admin@plotsync.com --superadmin-password changeme
"""
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import ProgrammingError

from apps.tenants.models import Company, Domain

User = get_user_model()


class Command(BaseCommand):
    help = "Bootstrap public tenant, optional demo tenant, and platform superadmin. Idempotent."

    def add_arguments(self, parser):
        site_domain = _strip_domain(os.environ.get("SITE_DOMAIN", "localhost"))

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
            help="Additional domains to map to the public tenant",
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
            default=f"demo.{site_domain}",
            help="Domain for the demo tenant",
        )
        parser.add_argument(
            "--demo-name",
            default="Demo Company",
            help="Display name for the demo tenant",
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
        all_domains = [
            primary_domain,
            *[_strip_domain(d) for d in (options["extra_domains"] or []) if _strip_domain(d) != primary_domain],
        ]

        tenant, created = _get_or_create_tenant(
            schema_name="public",
            name=options["name"],
            slug="public",
            admin_email=options["admin_email"],
        )
        self.stdout.write(
            self.style.SUCCESS(f"{'Created' if created else 'Exists'}: public tenant '{tenant.name}'")
        )
        _ensure_domains(self, tenant, all_domains)

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
            _ensure_domains(self, demo_tenant, [_strip_domain(options["demo_domain"])])

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


def _ensure_domains(cmd, tenant, domains):
    for i, domain_str in enumerate(domains):
        obj, created = Domain.objects.get_or_create(
            domain=domain_str,
            defaults={"tenant": tenant, "is_primary": i == 0},
        )
        cmd.stdout.write(
            cmd.style.SUCCESS(f"  {'Added' if created else 'Exists'} domain: {domain_str}")
        )
