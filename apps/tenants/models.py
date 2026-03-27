# apps/tenants/models.py
"""
Multi-Tenant Models for PlotSync SaaS Architecture

This module contains the core tenant (Company) and domain models that define
the multi-tenant structure. All tenant-specific data is stored in isolated
database schemas, while these models live in the public schema.
"""

from django.db import models
from django.utils import timezone
from django.core.validators import RegexValidator
from django_tenants.models import TenantMixin, DomainMixin
from datetime import timedelta
import uuid


class Company(TenantMixin):
    """
    Represents a Tenant (customer organization) in the PlotSync SaaS platform.
    
    Inherits from django_tenants.models.TenantMixin which provides:
    - auto_create_schema=True: Automatically creates schema on save
    - auto_drop_schema=True: Automatically drops schema on delete (optional config)
    - domain_url: Used for routing requests to correct tenant
    
    Each Company gets its own PostgreSQL schema where all tenant-specific
    data (Users, Clients, Documents, etc.) is stored.
    
    Example:
        # Create a company
        company = Company.objects.create(
            name="GGI Surveys",
            schema_name="ggi_surveys"
        )
        # Then create a domain for the company
        Domain.objects.create(
            domain="ggi.plotsync.local",
            tenant=company,
            is_primary=True
        )
    """

    auto_create_schema = True
    
    # ==================== CORE IDENTITY ====================
    name = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="Official company/organization name (e.g., 'GGI Surveys', 'Water Fiti')"
    )
    slug = models.SlugField(
        max_length=100,
        unique=True,
        db_index=True,
        help_text="URL-safe identifier derived from name"
    )
    
    # ==================== DATABASE & SCHEMA ====================
    # schema_name is inherited from TenantMixin
    # It defines which PostgreSQL schema this tenant uses
    
    # ==================== TENANT STATUS & LIFECYCLE ====================
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="If False, tenant is suspended (data preserved, access denied)"
    )
    created_on = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="When the tenant account was created"
    )
    updated_on = models.DateTimeField(
        auto_now=True,
        help_text="Last time tenant configuration was modified"
    )
    
    # ==================== SUBSCRIPTION & BILLING ====================
    PLAN_CHOICES = [
        ('starter', 'Starter - Basic Features'),
        ('professional', 'Professional - Advanced Features'),
        ('enterprise', 'Enterprise - Custom Solution'),
    ]
    
    plan = models.CharField(
        max_length=20,
        choices=PLAN_CHOICES,
        default='starter',
        help_text="Current subscription tier"
    )
    
    paid_until = models.DateField(
        null=True,
        blank=True,
        help_text="Date when current subscription expires (None = unpaid/trial)"
    )
    
    trial_period_days = models.IntegerField(
        default=30,
        help_text="Number of trial days (30 days from creation)"
    )
    
    @property
    def is_on_trial(self):
        """Check if tenant is still within trial period."""
        if self.paid_until is None:
            trial_end = self.created_on.date() + timedelta(days=self.trial_period_days)
            return timezone.now().date() < trial_end
        return False
    
    @property
    def is_expired(self):
        """Check if tenant subscription has expired (not on trial, no paid_until)."""
        if self.paid_until is None and not self.is_on_trial:
            return True
        if self.paid_until and timezone.now().date() > self.paid_until:
            return True
        return False
    
    @property
    def days_until_expiry(self):
        """Days remaining until expiration (None if expired, negative if overdue)."""
        target_date = self.paid_until or (self.created_on.date() + timedelta(days=self.trial_period_days))
        delta = (target_date - timezone.now().date()).days
        return delta
    
    def set_subscription(self, plan, months_duration):
        """
        Programmatically update subscription.
        
        Args:
            plan (str): One of 'starter', 'professional', 'enterprise'
            months_duration (int): How many months to add
        """
        self.plan = plan
        self.paid_until = timezone.now().date() + timedelta(days=30 * months_duration)
        self.save()
    
    # ==================== COMPANY METADATA ====================
    logo = models.ImageField(
        upload_to='company_logos/',
        null=True,
        blank=True,
        help_text="Company branding logo"
    )
    description = models.TextField(
        blank=True,
        help_text="Company description for admin/support purposes"
    )
    phone = models.CharField(
        max_length=20,
        blank=True,
        help_text="Main company phone number"
    )
    email = models.EmailField(
        blank=True,
        help_text="Main company email"
    )
    website = models.URLField(
        blank=True,
        help_text="Company website URL"
    )
    
    # ==================== ADMIN CONTACT ====================
    admin_email = models.EmailField(
        help_text="Email of primary tenant administrator (for billing/support notifications)"
    )
    admin_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Name of primary administrator"
    )
    
    # ==================== USAGE TRACKING ====================
    max_users = models.IntegerField(
        default=10,
        help_text="Maximum number of users allowed (based on plan)"
    )
    max_clients = models.IntegerField(
        default=100,
        help_text="Maximum number of clients (based on plan)"
    )
    max_storage_gb = models.IntegerField(
        default=10,
        help_text="Maximum storage in GB (based on plan)"
    )
    
    # ==================== INTERNAL TRACKING ====================
    uuid = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        help_text="Globally unique identifier for this tenant"
    )
    notes = models.TextField(
        blank=True,
        help_text="Internal notes (for support/admin purposes)"
    )
    
    class Meta:
        ordering = ['created_on']
        verbose_name = 'Company (Tenant)'
        verbose_name_plural = 'Companies (Tenants)'
        indexes = [
            models.Index(fields=['is_active', 'created_on']),
            models.Index(fields=['plan']),
            models.Index(fields=['slug']),
        ]
    
    def __str__(self):
        status = "✓ Active" if self.is_active else "✗ Inactive"
        return f"{self.name} ({self.plan.title()} - {status})"
    
    def __repr__(self):
        return f"<Company: {self.name} | Schema: {self.schema_name}>"
    
    def save(self, *args, **kwargs):
        """
        Override save to auto-generate slug from name if not provided.
        django-tenants will handle schema creation automatically.
        """
        if not self.slug:
            from django.utils.text import slugify
            self.slug = slugify(self.name)[:100]
        super().save(*args, **kwargs)


class Domain(DomainMixin):
    """
    Represents a domain/subdomain linked to a Company (Tenant).
    
    Inherits from django_tenants.models.DomainMixin which provides:
    - tenant: ForeignKey to Company
    - domain: The actual domain/subdomain
    - is_primary: Whether this is the primary domain for the tenant
    
    Multiple domains can point to the same tenant, allowing:
    - Subdomains: ggi.plotsync.com, water-fiti.plotsync.com
    - Custom domains: surveyor.ggi.com
    - White-labeled domains: customdomain.com
    
    Examples:
        # Primary subdomain
        Domain.objects.create(domain='ggi.plotsync.com', tenant=company, is_primary=True)
        
        # Custom domain
        Domain.objects.create(domain='surveyor.ggi.com', tenant=company, is_primary=False)
    """
    
    tenant = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='domains',
        help_text="The company/tenant this domain belongs to"
    )
    domain = models.CharField(
        max_length=253,
        unique=True,
        db_index=True,
        validators=[
            RegexValidator(
                regex=r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$",
                message="Enter a valid domain like tenant.plotsync.local (no http/https).",
            )
        ],
        help_text="Full domain name (e.g., 'ggi.plotsync.local', 'surveyor.ggi.com')"
    )
    is_primary = models.BooleanField(
        default=False,
        help_text="If True, this is the default domain for the tenant"
    )
    created_on = models.DateTimeField(
        auto_now_add=True,
        help_text="When this domain was added"
    )
    
    class Meta:
        ordering = ['is_primary', '-created_on']
        verbose_name = 'Domain'
        verbose_name_plural = 'Domains'
        indexes = [
            models.Index(fields=['tenant', 'is_primary']),
            models.Index(fields=['domain']),
        ]
    
    def __str__(self):
        primary = "(Primary)" if self.is_primary else ""
        return f"{self.domain} → {self.tenant.name} {primary}"
    
    def __repr__(self):
        return f"<Domain: {self.domain}>"


class SubscriptionPayment(models.Model):
    """
    Records a payment made by a Company to PlotSync for their SaaS subscription.

    Lives in the public schema (not in tenant schemas) because it tracks
    platform-level billing, not tenant-internal finances.
    On save, automatically advances the company's paid_until via set_subscription().
    """

    PLAN_CHOICES = Company.PLAN_CHOICES

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='subscription_payments',
        help_text="The tenant company this payment was made for",
    )
    plan = models.CharField(
        max_length=20,
        choices=PLAN_CHOICES,
        help_text="Plan tier purchased",
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Amount paid",
    )
    months_purchased = models.PositiveIntegerField(
        default=1,
        help_text="Number of months this payment covers",
    )
    payment_date = models.DateField(
        default=timezone.now,
        db_index=True,
        help_text="Date the payment was received",
    )
    reference = models.CharField(
        max_length=255,
        blank=True,
        help_text="Payment reference number / transaction ID",
    )
    notes = models.TextField(
        blank=True,
        help_text="Internal notes about this payment",
    )
    recorded_by = models.CharField(
        max_length=255,
        blank=True,
        help_text="Username of platform admin who recorded this payment",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-payment_date', '-created_at']
        verbose_name = 'Subscription Payment'
        verbose_name_plural = 'Subscription Payments'
        indexes = [
            models.Index(fields=['company', 'payment_date']),
        ]

    def __str__(self):
        return f"{self.company.name} — {self.get_plan_display()} x{self.months_purchased}mo — {self.payment_date}"

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        super().save(*args, **kwargs)
        # Only apply entitlement extension when creating a new payment row.
        if is_new:
            self.company.set_subscription(self.plan, self.months_purchased)


# =====================================================================
# EXAMPLE USAGE & PROGRAMMATIC TENANT CREATION
# =====================================================================

"""
CREATING A NEW TENANT PROGRAMMATICALLY
========================================

Below is how you would create a new tenant (company) and assign domains.
This would typically happen in a signup view or admin action.

Example 1: Create "GGI Surveys" tenant
---------------------------------------

from apps.tenants.models import Company, Domain

# Step 1: Create the Company (Tenant)
company = Company.objects.create(
    name="GGI Surveys",
    slug="ggi-surveys",
    schema_name="ggi_surveys",  # PostgreSQL schema name
    admin_email="admin@ggisurveys.com",
    admin_name="John Admin",
    plan="professional",
    paid_until="2025-12-31",
    phone="+254722123456",
    email="info@ggisurveys.com",
    is_active=True,
)

# Step 2: Create primary domain
primary_domain = Domain.objects.create(
    tenant=company,
    domain="ggi.plotsync.local",
    is_primary=True
)

# Step 3: Add optional custom domain
custom_domain = Domain.objects.create(
    tenant=company,
    domain="surveyor.ggi.com",
    is_primary=False
)

# Now requests to ggi.plotsync.local will route to GGI Surveys' schema


Example 2: Create "Water Fiti" tenant (on trial)
-------------------------------------------------

from apps.tenants.models import Company, Domain
from datetime import timedelta
from django.utils import timezone

company = Company.objects.create(
    name="Water Fiti",
    slug="water-fiti",
    schema_name="water_fiti",
    admin_email="admin@waterfiti.com",
    admin_name="Jane Doe",
    plan="starter",  # Free trial plan
    paid_until=None,  # No paid subscription yet (will use trial_period_days)
    phone="+254700654321",
    is_active=True,
)

# Create primary domain
Domain.objects.create(
    tenant=company,
    domain="water-fiti.plotsync.local",
    is_primary=True
)

# Check trial status
print(f"On Trial? {company.is_on_trial}")  # True
print(f"Days remaining: {company.days_until_expiry}")  # e.g., 28


Example 3: Upgrade a subscription
----------------------------------

company = Company.objects.get(slug="ggi-surveys")
company.set_subscription(plan='enterprise', months_duration=12)

# Now:
# - company.plan = 'enterprise'
# - company.paid_until = 12 months from now


Example 4: Check tenant status in views
----------------------------------------

from apps.tenants.models import Company

company = Company.objects.get(schema_name='ggi_surveys')

if company.is_active:
    print("✓ Tenant can access the system")
else:
    print("✗ Tenant is suspended")

if company.is_expired:
    print("⚠ Subscription has expired - prompt for renewal")
elif company.is_on_trial:
    print(f"ℹ On trial - {company.days_until_expiry} days remaining")
else:
    print(f"✓ Subscription active until {company.paid_until}")


QUERYING ACROSS TENANTS (Public Schema)
========================================

Since Company and Domain live in the PUBLIC schema, you can query
all tenants from the public schema without switching:

# Get all active companies
active_companies = Company.objects.filter(is_active=True)

# Get all companies on 'professional' plan
professional = Company.objects.filter(plan='professional')

# Get a specific company and all its domains
company = Company.objects.prefetch_related('domains').get(slug='ggi-surveys')
for domain in company.domains.all():
    print(f"  - {domain.domain}")

# Find which tenant owns a domain
domain_obj = Domain.objects.get(domain='ggi.plotsync.local')
print(f"This domain belongs to: {domain_obj.tenant.name}")
"""
