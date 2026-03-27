# apps/tenants/views.py
"""
Platform-admin views for managing tenants (Companies) and their subscriptions.
All views require is_superuser — regular tenant users never see these.
"""

import re
import secrets

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import UserPassesTestMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import RegexValidator
from django.db import IntegrityError, transaction, connection
from django.db.models import Sum, Count
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.text import slugify
from django.views import View
from django.views.generic import TemplateView
from django_tenants.utils import tenant_context

from .models import Company, Domain, SubscriptionPayment


# ─────────────────────────────────────────────────────────────────────────────
# Mixin
# ─────────────────────────────────────────────────────────────────────────────

class SuperAdminRequired(UserPassesTestMixin):
    """All subscription views are restricted to platform superadmins only."""
    raise_exception = True

    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.is_superuser

    def dispatch(self, request, *args, **kwargs):
        # Platform subscription management must run on public schema only.
        if connection.schema_name != "public":
            raise PermissionDenied("Platform subscription management is only available on the public domain.")
        return super().dispatch(request, *args, **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Tenant List
# ─────────────────────────────────────────────────────────────────────────────

class TenantListView(SuperAdminRequired, TemplateView):
    template_name = "subscriptions/tenant_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        companies = (
            Company.objects
            .exclude(schema_name="public")
            .prefetch_related("domains")
            .annotate(payment_count=Count("subscription_payments"))
        )
        ctx["companies"] = companies
        ctx["total"] = companies.count()
        ctx["active"] = companies.filter(is_active=True).count()
        ctx["expired"] = sum(1 for c in companies if c.is_expired)
        ctx["today"] = timezone.now().date()
        return ctx


# ─────────────────────────────────────────────────────────────────────────────
# 2. Onboard (3-step wizard — single POST)
# ─────────────────────────────────────────────────────────────────────────────

class TenantOnboardView(SuperAdminRequired, View):
    template_name = "subscriptions/tenant_onboard.html"

    def get(self, request):
        from django.shortcuts import render
        return render(
            request,
            self.template_name,
            {
                "plans": Company.PLAN_CHOICES,
                "form_data": _build_onboard_form_data(),
            },
        )

    def post(self, request):
        from django.shortcuts import render

        name = request.POST.get("name", "").strip()
        domain = _normalize_domain(request.POST.get("domain", "").strip())
        admin_email = request.POST.get("admin_email", "").strip()
        admin_name = request.POST.get("admin_name", "").strip()
        plan = request.POST.get("plan", "starter")
        max_users = _safe_int(request.POST.get("max_users"), 10)
        max_clients = _safe_int(request.POST.get("max_clients"), 100)
        max_storage_gb = _safe_int(request.POST.get("max_storage_gb"), 10)
        trial_period_days = _safe_int(request.POST.get("trial_period_days"), 30)
        paid_until = request.POST.get("paid_until") or None
        # optional initial payment
        initial_amount = request.POST.get("initial_amount", "").strip()
        initial_reference = request.POST.get("initial_reference", "").strip()
        months_purchased = _safe_int(request.POST.get("months_purchased"), 1)

        # --- Validate ---
        errors = []
        if not name:
            errors.append("Company name is required.")
        if not domain:
            errors.append("Domain is required.")
        if not admin_email:
            errors.append("Admin email is required.")
        if plan not in dict(Company.PLAN_CHOICES):
            errors.append("Invalid plan selected.")
        if max_users < 1 or max_clients < 1 or max_storage_gb < 1:
            errors.append("Limits must be at least 1.")
        if trial_period_days < 0:
            errors.append("Trial period cannot be negative.")
        if months_purchased < 1:
            errors.append("Months purchased must be at least 1.")
        if domain:
            try:
                RegexValidator(
                    regex=r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$",
                    message="Enter a valid domain like tenant.plotsync.local (no http/https).",
                )(domain)
            except ValidationError:
                errors.append("Enter a valid domain like tenant.plotsync.local (no http/https).")
        if Company.objects.filter(name=name).exists():
            errors.append(f"A company named '{name}' already exists.")
        if Domain.objects.filter(domain=domain).exists():
            errors.append(f"Domain '{domain}' is already in use.")

        schema_name = _to_schema_name(name)
        if Company.objects.filter(schema_name=schema_name).exists():
            schema_name = _to_schema_name(name + "_" + str(timezone.now().year))

        if errors:
            messages.error(request, " ".join(errors))
            return render(request, self.template_name, {
                "plans": Company.PLAN_CHOICES,
                "form_data": _build_onboard_form_data(request.POST),
            })

        try:
            with transaction.atomic():
                # --- Create tenant ---
                company = Company.objects.create(
                    name=name,
                    slug=slugify(name)[:100],
                    schema_name=schema_name,
                    admin_email=admin_email,
                    admin_name=admin_name,
                    plan=plan,
                    max_users=max_users,
                    max_clients=max_clients,
                    max_storage_gb=max_storage_gb,
                    trial_period_days=trial_period_days,
                    is_active=True,
                )
                if paid_until:
                    from datetime import date
                    company.paid_until = date.fromisoformat(paid_until)
                    company.save()

                Domain.objects.create(domain=domain, tenant=company, is_primary=True)

                # Optional initial payment record
                if initial_amount:
                    SubscriptionPayment.objects.create(
                        company=company,
                        plan=plan,
                        amount=initial_amount,
                        months_purchased=months_purchased,
                        reference=initial_reference,
                        recorded_by=request.user.username,
                    )
        except (IntegrityError, ValueError) as exc:
            messages.error(request, f"Could not onboard company: {exc}")
            return render(request, self.template_name, {
                "plans": Company.PLAN_CHOICES,
                "form_data": _build_onboard_form_data(request.POST),
            })

        created_admin = _ensure_default_tenant_superadmin(company)
        if created_admin:
            messages.success(
                request,
                (
                    f"Tenant admin created for '{company.name}'. Username: {created_admin['username']} "
                    f"Temporary password: {created_admin['password']}"
                ),
            )

        messages.success(request, f"Company '{name}' onboarded successfully. Schema '{schema_name}' created.")
        return redirect("tenant_list")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Tenant Detail
# ─────────────────────────────────────────────────────────────────────────────

class TenantDetailView(SuperAdminRequired, TemplateView):
    template_name = "subscriptions/tenant_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        company = get_object_or_404(Company, slug=kwargs["slug"])
        primary_domain = company.domains.filter(is_primary=True).first()
        payments = company.subscription_payments.order_by("-payment_date", "-created_at")
        total_paid = payments.aggregate(total=Sum("amount"))["total"] or 0
        ctx.update({
            "company": company,
            "domains": company.domains.all(),
            "primary_domain": primary_domain,
            "payments": payments[:20],
            "total_paid": total_paid,
            "plans": Company.PLAN_CHOICES,
            "today": timezone.now().date(),
        })
        return ctx


class TenantUpdateView(SuperAdminRequired, View):
    """POST-only: update tenant metadata and the primary domain."""

    def post(self, request, slug):
        company = get_object_or_404(Company, slug=slug)
        primary_domain = company.domains.filter(is_primary=True).first()

        name = request.POST.get("name", "").strip()
        admin_email = request.POST.get("admin_email", "").strip()
        admin_name = request.POST.get("admin_name", "").strip()
        company_email = request.POST.get("email", "").strip()
        phone = request.POST.get("phone", "").strip()
        website = request.POST.get("website", "").strip()
        description = request.POST.get("description", "").strip()
        notes = request.POST.get("notes", "").strip()
        domain = _normalize_domain(request.POST.get("domain", "").strip())

        errors = []
        if not name:
            errors.append("Company name is required.")
        if not admin_email:
            errors.append("Admin email is required.")
        if not domain:
            errors.append("Primary domain is required.")
        if name and Company.objects.exclude(pk=company.pk).filter(name=name).exists():
            errors.append(f"A company named '{name}' already exists.")
        if domain:
            try:
                RegexValidator(
                    regex=r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$",
                    message="Enter a valid domain like tenant.127.0.0.1.sslip.io (no http/https).",
                )(domain)
            except ValidationError:
                errors.append("Enter a valid domain like tenant.127.0.0.1.sslip.io (no http/https).")
            if Domain.objects.exclude(pk=getattr(primary_domain, "pk", None)).filter(domain=domain).exists():
                errors.append(f"Domain '{domain}' is already in use.")

        if errors:
            messages.error(request, " ".join(errors))
            return redirect("tenant_detail", slug=company.slug)

        try:
            with transaction.atomic():
                company.name = name
                company.admin_email = admin_email
                company.admin_name = admin_name
                company.email = company_email
                company.phone = phone
                company.website = website
                company.description = description
                company.notes = notes
                company.save()

                if primary_domain:
                    primary_domain.domain = domain
                    primary_domain.is_primary = True
                    primary_domain.save()
                else:
                    Domain.objects.create(domain=domain, tenant=company, is_primary=True)
        except (IntegrityError, ValueError, ValidationError) as exc:
            messages.error(request, f"Could not update tenant details: {exc}")
            return redirect("tenant_detail", slug=company.slug)

        created_admin = _ensure_default_tenant_superadmin(company)
        if created_admin:
            messages.success(
                request,
                (
                    f"No users existed in '{company.name}', so a tenant superadmin was created. "
                    f"Username: {created_admin['username']} Temporary password: {created_admin['password']}"
                ),
            )

        messages.success(request, f"Tenant '{company.name}' updated successfully.")
        return redirect("tenant_detail", slug=company.slug)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Subscription Setup (update plan + record payment)
# ─────────────────────────────────────────────────────────────────────────────

class SubscriptionSetupView(SuperAdminRequired, View):
    """POST-only: update plan/limits and optionally record a payment."""

    def post(self, request, slug):
        company = get_object_or_404(Company, slug=slug)

        plan = request.POST.get("plan", company.plan)
        paid_until = request.POST.get("paid_until", "").strip()
        max_users = request.POST.get("max_users", "").strip()
        max_clients = request.POST.get("max_clients", "").strip()
        max_storage_gb = request.POST.get("max_storage_gb", "").strip()
        is_active = request.POST.get("is_active") == "1"
        amount = request.POST.get("amount", "").strip()
        months_purchased = _safe_int(request.POST.get("months_purchased"), 1)
        reference = request.POST.get("reference", "").strip()
        notes = request.POST.get("notes", "").strip()

        # Update company fields
        company.plan = plan
        company.is_active = is_active
        if paid_until:
            from datetime import date
            company.paid_until = date.fromisoformat(paid_until)
        if max_users:
            company.max_users = int(max_users)
        if max_clients:
            company.max_clients = int(max_clients)
        if max_storage_gb:
            company.max_storage_gb = int(max_storage_gb)
        company.save()

        # Record payment if amount provided
        if amount:
            SubscriptionPayment.objects.create(
                company=company,
                plan=plan,
                amount=amount,
                months_purchased=months_purchased,
                payment_date=timezone.now().date(),
                reference=reference,
                notes=notes,
                recorded_by=request.user.username,
            )
            messages.success(request, f"Payment recorded and subscription updated for '{company.name}'.")
        else:
            messages.success(request, f"Subscription updated for '{company.name}'.")

        return redirect("tenant_detail", slug=slug)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Transactions
# ─────────────────────────────────────────────────────────────────────────────

class TransactionsListView(SuperAdminRequired, TemplateView):
    template_name = "subscriptions/transactions.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = SubscriptionPayment.objects.select_related("company").order_by("-payment_date", "-created_at")

        # Filters from GET params
        company_slug = self.request.GET.get("company", "")
        plan_filter = self.request.GET.get("plan", "")
        date_from = self.request.GET.get("date_from", "")
        date_to = self.request.GET.get("date_to", "")

        if company_slug:
            qs = qs.filter(company__slug=company_slug)
        if plan_filter:
            qs = qs.filter(plan=plan_filter)
        if date_from:
            qs = qs.filter(payment_date__gte=date_from)
        if date_to:
            qs = qs.filter(payment_date__lte=date_to)

        total = qs.aggregate(total=Sum("amount"))["total"] or 0

        ctx.update({
            "payments": qs[:200],
            "total": total,
            "companies": Company.objects.exclude(schema_name="public").order_by("name"),
            "plans": Company.PLAN_CHOICES,
            "filters": {
                "company": company_slug,
                "plan": plan_filter,
                "date_from": date_from,
                "date_to": date_to,
            },
        })
        return ctx


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _to_schema_name(text: str) -> str:
    """Convert arbitrary text to a valid PostgreSQL schema identifier."""
    slug = re.sub(r"[^a-z0-9_]", "_", text.lower().strip())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug[:63] or "tenant"


def _safe_int(value, default: int) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _normalize_domain(value: str) -> str:
    for prefix in ("https://", "http://"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    value = value.split("/")[0]
    value = value.split(":")[0]
    return value.strip().lower()


def _build_onboard_form_data(post_data=None) -> dict:
    """Return a complete form-data mapping expected by the onboarding template."""
    defaults = {
        "name": "",
        "domain": "",
        "admin_email": "",
        "admin_name": "",
        "plan": "starter",
        "paid_until": "",
        "max_users": "10",
        "max_clients": "100",
        "max_storage_gb": "10",
        "trial_period_days": "30",
        "initial_amount": "",
        "months_purchased": "1",
        "initial_reference": "",
    }
    if not post_data:
        return defaults

    merged = defaults.copy()
    for key in defaults:
        value = post_data.get(key)
        if value is not None:
            merged[key] = value
    return merged


def _ensure_default_tenant_superadmin(company: Company):
    """Create a default tenant superadmin when the tenant schema has no users."""
    User = get_user_model()

    with tenant_context(company):
        if User.objects.exists():
            return None

        username = _build_tenant_admin_username(company)
        password = secrets.token_urlsafe(12)
        first_name, last_name = _split_name(company.admin_name)
        user = User.objects.create_superuser(
            username=username,
            email=company.admin_email,
            password=password,
        )
        if hasattr(user, "first_name"):
            user.first_name = first_name
        if hasattr(user, "last_name"):
            user.last_name = last_name
        user.save()

    return {"username": username, "password": password}


def _build_tenant_admin_username(company: Company) -> str:
    email_local_part = (company.admin_email or "").split("@", 1)[0]
    candidate = slugify(email_local_part).replace("-", "_")
    if not candidate:
        candidate = f"{slugify(company.slug or company.schema_name).replace('-', '_')}_admin"
    return candidate[:150]


def _split_name(value: str):
    parts = (value or "").strip().split(None, 1)
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]
