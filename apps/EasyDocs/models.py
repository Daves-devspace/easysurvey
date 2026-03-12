import mimetypes
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from django.db import models
from django.db.models import Sum, F, Value, Q, DecimalField
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.core.cache import cache
from django.utils.functional import cached_property


import logging

logger = logging.getLogger(__name__)

# Office Documents
# -----------------------
# File upload paths
# -----------------------


def validate_mime(file, allowed_mimes):
    mime_type = mimetypes.guess_type(file.name)[0]
    if mime_type not in allowed_mimes:
        raise ValidationError(f"File type {mime_type} not allowed")
    
    

# File upload paths and validators
def validate_file_size(file):
    max_size = 10 * 1024 * 1024  # 10MB
    if file.size > max_size:
        raise ValidationError(f"File size must be under {max_size//1024//1024}MB")

def office_document_path(instance, filename):
    """Organize office documents by type/year/month with better naming"""
    now = timezone.now()
    doc_type_slug = instance.doc_type.name.lower().replace(' ', '_')
    safe_filename = f"{now.strftime('%Y%m%d_%H%M%S')}_{filename}"
    return f"office/{doc_type_slug}/{now.year}/{now.month:02d}/{safe_filename}"

def client_document_path(instance, filename):
    """Organize client documents with client context"""
    now = timezone.now()
    client_slug = f"client_{instance.client.id}"
    doc_type_slug = instance.doc_type.name.lower().replace(' ', '_')
    safe_filename = f"{now.strftime('%Y%m%d_%H%M%S')}_{filename}"
    return f"clients/{client_slug}/{doc_type_slug}/{now.year}/{now.month:02d}/{safe_filename}"

# MIME type constants
ALLOWED_CLIENT_DOC_MIME_TYPES = [
    'application/pdf', 'image/jpeg', 'image/png', 
    'application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
]

ALLOWED_OFFICE_MIME_TYPES = ALLOWED_CLIENT_DOC_MIME_TYPES + [
    'text/plain', 'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
]




# Gender choices
class Gender(models.TextChoices):
    MALE = 'Male', 'Male'
    FEMALE = 'Female', 'Female'
    OTHER = 'Others', 'Others'

class ServiceCategory(models.TextChoices):
    TITLE   = 'title',   'Title Deed Service'
    GROUND  = 'ground',  'Ground Service'
    OTHERS  = 'others',  'Other Services'




# models.py
class DriveOAuthToken(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='drive_oauth_token')
    refresh_token_encrypted = models.TextField(blank=True, null=True)
    access_token_encrypted = models.TextField(blank=True, null=True)
    token_expiry = models.DateTimeField(blank=True, null=True)
    scopes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    needs_reauth = models.BooleanField(default=False)

    
    def __str__(self):
        return f"Drive OAuth - {self.user.username}"



class SiteSettings(models.Model):
    # Enforce only one row
    singleton_enforcer = models.BooleanField(default=True, editable=False, unique=True)  
    company_name = models.CharField(max_length=200, default="Plotsync")
    logo = models.ImageField(upload_to="company/", blank=True, null=True)
    company_phone = models.CharField(max_length=20, blank=True, null=True)
    company_email = models.EmailField(unique=True, blank=True, null=True, db_index=True)
    tagline = models.CharField(max_length=255, blank=True, default="Thank you for letting us serve you!")
    stamp_signature = models.ImageField(upload_to="company/", blank=True, null=True)
    
    updated_at = models.DateTimeField(auto_now=True)
    
    allow_employee_sms = models.BooleanField(
        default=False,
        help_text="If enabled, employees will also receive bulk SMS messages"
    )

    allow_employee_email = models.BooleanField(
        default=False,
        help_text="If enabled, employees will also receive bulk email messages"
    )

    # optional: who exactly?
    employee_sms_roles = models.JSONField(
        default=list,
        blank=True,
        help_text="Roles allowed to receive SMS e.g. ['Admin', 'Surveyor']"
    )
    
    # GOOGLE DRIVE CONFIGURATION
    
    google_drive_enabled = models.BooleanField(default=False)
    google_drive_root_folder_id = models.CharField(max_length=255, blank=True, null=True)
    google_drive_service_account_key_encrypted = models.TextField(blank=True, null=True)
    google_drive_service_account_email = models.CharField(max_length=255, blank=True, null=True)
    
    google_oauth_client_id = models.CharField(max_length=255, blank=True, null=True)
    google_oauth_client_secret_encrypted = models.TextField(blank=True, null=True)
    google_oauth_redirect_uris = models.TextField(
        blank=True,
        default="",
        help_text="Allowed OAuth redirect URIs (one per line or comma-separated). Example: http://localhost:8080/drive/oauth/callback/"
    )
    
    
    # Configuration
    drive_auto_folder_creation = models.BooleanField(default=True)
    drive_file_naming_pattern = models.CharField(
        max_length=500, 
        default="{client_id}/{year}/{month}/{filename}",
        help_text="Available variables: {client_id}, {year}, {month}, {day}, {filename}"
    )
    
    # Status tracking
    drive_config_status = models.CharField(
        max_length=20, 
        choices=[
            ('not_configured', 'Not Configured'),
            ('configured', 'Configured'),
            ('testing', 'Testing'),
            ('error', 'Error')
        ],
        default='not_configured'
    )
    drive_last_test_status = models.TextField(blank=True, null=True)
    drive_last_test_at = models.DateTimeField(blank=True, null=True)
    drive_config_updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    drive_config_updated_at = models.DateTimeField(auto_now=True)
    

    class Meta:
            verbose_name_plural = "Site Settings"
        
    def __str__(self):
            return "Site Configuration"
        
    def is_google_drive_ready(self):
        """
        Check if Google Drive is ready for use.
        During testing, we should still allow storage initialization.
        """
        return (self.google_drive_enabled and 
                self.google_drive_service_account_key_encrypted and
                self.drive_config_status in ['configured', 'testing'])  # ← Allow 'testing' status




# Document Type
class DocType(models.Model):
    name = models.CharField(max_length=100, help_text="Enter document type name")
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name



    
    

# Client Model
class Client(models.Model):
    first_name = models.CharField(max_length=100, db_index=True)
    last_name = models.CharField(max_length=100, db_index=True)
    email = models.EmailField(unique=True, blank=True, null=True, db_index=True)
    phone = models.CharField(max_length=15, unique=True, db_index=True)
    profile_pic = models.ImageField(
        upload_to='clients/avatars/',
        blank=True,
        null=True,
        help_text="Client profile picture (optional)"
    )
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.first_name} {self.last_name}"

    def get_profile_pic_url(self):
        """
        Returns the URL to the profile pic or a static fallback path.
        Use this in templates to avoid checking existence everywhere.
        """
        if self.profile_pic and hasattr(self.profile_pic, 'url'):
            return self.profile_pic.url
        # change this path to match your static fallback image
        from django.templatetags.static import static
        return static('assets/images/user/avatar-5.jpg')


# Service Model
class Service(models.Model):
    CATEGORY_CHOICES = ServiceCategory.choices

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # NEW FIELDS
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default=ServiceCategory.TITLE)
    expected_duration_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Default expected duration in days for this service"
    )


    requires_title_collection = models.BooleanField(
        default=False,
        help_text="If true, after the final process we show Confirm & Collect buttons; otherwise just Complete"
    )

    def __str__(self):
        return f"{self.name}"

    def update_total_price(self):
        agg = self.processes.aggregate(total=Sum('cost'))
        self.total_price = agg['total'] or 0
        self.save(update_fields=['total_price'])


# Process Model
class Process(models.Model):
    service = models.ForeignKey(Service, related_name='processes', on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    step_order = models.PositiveIntegerField()
    cost = models.DecimalField(max_digits=10, decimal_places=2)
    message = models.TextField()  # Message to be sent to client
    notification_enabled = models.BooleanField(
        default=True,
        help_text="Global setting: enable automated notifications when this process completes"
    )

    class Meta:
        ordering = ['step_order']
        constraints = [
            models.UniqueConstraint(fields=['service', 'step_order'], name='unique_step_per_service')
        ]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.service.update_total_price()

    def delete(self, *args, **kwargs):
        service = self.service
        super().delete(*args, **kwargs)
        service.update_total_price()

    def __str__(self):
        return f"{self.service.name} – {self.name} "

# Client Service Model
class ClientService(models.Model):
    client = models.ForeignKey(
        'Client', related_name='client_services', on_delete=models.CASCADE
    )
    service = models.ForeignKey(
        'Service', related_name='client_services', on_delete=models.CASCADE
    )
    land_description = models.CharField(max_length=255)
    requested_at = models.DateTimeField(auto_now_add=True)
    # Employee Assignment Fields
    assigned_employee = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='assigned_services',
        help_text="Employee assigned to this service"
    )
    ASSIGNMENT_STATUS_CHOICES = [
        ('unassigned', 'Unassigned'),
        ('pending_acceptance', 'Pending Acceptance'),
        ('accepted', 'Accepted'),
        ('declined', 'Declined'),
        ('reassigned', 'Reassigned'),
    ]
    assignment_status = models.CharField(
        max_length=20,
        choices=ASSIGNMENT_STATUS_CHOICES,
        default='unassigned',
        help_text="Current assignment status"
    )

    # Deadline Fields
    expected_duration_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Override service default expected duration"
    )
    deadline = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Calculated deadline based on assignment + duration"
    )
    deadline_extended = models.BooleanField(
        default=False,
        help_text="True if deadline has been extended"
    )
    original_deadline = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Original deadline before any extensions"
    )


    updated_at = models.DateTimeField(auto_now=True)

    overridden_total_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    full_total_price = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        help_text="Denormalized total for fast reads"
    )

    STATUS_CHOICES = [
        ('active', 'Active'),
        ('completed', 'Completed'),
        ('collected', 'Title Deed Collected'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['client', 'service', 'land_description'],
                condition=Q(status='active'),
                name='unique_active_service_per_land'
            )
        ]
        indexes = [
            models.Index(fields=['client', 'status']),
            models.Index(fields=['service', 'status']),
            models.Index(fields=['requested_at']),
        ]


    def save(self, *args, **kwargs):

        """
      Override save to ensure full_total_price is computed correctly after first save.
    Handles both process-based (TITLE) and non-process-based (GROUND) services.
               """

        is_new = self.pk is None

        if is_new:
            # Save initially to assign a primary key
            super().save(*args, **kwargs)

        # ✅ Step 1: Make sure service total is correct BEFORE calculating full_total_price
        if self.service.category == ServiceCategory.GROUND and self.service.total_price == 0:
            self.service.update_total_price()

        # ✅ Step 2: Calculate total now that service price is up-to-date
        self.full_total_price = self._calculate_full_total()

        # ✅ Step 3: Save full total to DB
        if is_new:
            super().save(update_fields=['full_total_price'])
        else:
            super().save(*args, **kwargs)

    def update_full_total(self, save=True):
        """
        Public method to recalculate and update the full_total_price field.
        """
        self.full_total_price = self._calculate_full_total()
        if save:
            self.save(update_fields=['full_total_price'])

    def _calculate_full_total(self) -> Decimal:
        """
        Compute the grand total (processes + sub-services).
        Uses overridden values if provided.
        """
        # Determine if service has processes by category
        if self.service.category == ServiceCategory.TITLE:
            # Aggregate cost of all processes
            proc_agg = self.service_processes.aggregate(
                total=Coalesce(
                    Sum(Coalesce(F('overridden_cost'), F('process__cost')),
                        output_field=DecimalField()),
                    Value(0), output_field=DecimalField()
                )
            )['total']
            proc_total = proc_agg or Decimal('0.00')
        else:
            # Use overridden total or fallback to service's total_price
            proc_total = (
                self.overridden_total_price
                if self.overridden_total_price is not None
                else self.service.total_price
            )

        # Sum all sub-service costs
        sub_agg = self.sub_services.aggregate(
            total=Coalesce(
                Sum(Coalesce(F('overridden_price'), F('sub_service__price')),
                    output_field=DecimalField()),
                Value(0), output_field=DecimalField()
            )
        )['total']
        sub_total = sub_agg or Decimal('0.00')

        # Return the grand total
        return proc_total + sub_total

    @cached_property
    def total_paid(self) -> Decimal:
        """
        Total net payments made toward this client service.
        Payment adjustments are compensating claw-backs and therefore
        reduce the effective paid amount.
        """
        paid = self.payments.aggregate(
            total=Coalesce(Sum('amount'), Value(0), output_field=DecimalField())
        )['total']
        adjusted = PaymentAdjustment.objects.filter(
            original_payment__client_service=self
        ).aggregate(
            total=Coalesce(Sum('amount'), Value(0), output_field=DecimalField())
        )['total']
        return max(Decimal('0.00'), Decimal(paid or 0) - Decimal(adjusted or 0))

    @cached_property
    def sub_services_total(self) -> Decimal:
        """
        Total cost of all sub-services (uses overridden price if available).
        """
        total = self.sub_services.aggregate(
            total=Coalesce(
                Sum(Coalesce(F('overridden_price'), F('sub_service__price')),
                    output_field=DecimalField()),
                Value(0), output_field=DecimalField()
            )
        )['total']
        return Decimal(total or 0)

    @cached_property
    def total_balance(self) -> Decimal:
        """
        Remaining balance after payments.
        """
        return max(Decimal('0.00'), self.full_total_price - self.total_paid)

    @property
    def payment_status(self) -> str:
        """
        Human-readable payment status.
        """
        balance = self.total_balance
        if balance <= 0:
            return 'Fully Paid'
        if self.total_paid > 0:
            return 'Partially Paid'
        return 'Not Paid'

    def __str__(self):
        return f"{self.service.name} for {self.land_description}"


class Booking(models.Model):
    client_service = models.ForeignKey(
        ClientService,
        on_delete=models.CASCADE,
        related_name='bookings'   # <-- allow many
    )
    created_at = models.DateTimeField(auto_now_add=True)
    scheduled_date = models.DateTimeField()
    dispatch_message = models.TextField(blank=True, null=True)

    handled = models.BooleanField(default=False)
    handled_at = models.DateTimeField(null=True, blank=True)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='handled_bookings',
        help_text="Who marked this booking as handled"
    )
    surveyors = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through='BookingAssignment',
        related_name='assigned_bookings',
        limit_choices_to={'groups__name': 'Surveyor'},
        help_text="Which surveyors were allocated"
    )
    
    class Meta:
        ordering = ['-created_at']  # 👈 Most recent first

    def generate_default_message(self):
        scheduled_local = timezone.localtime(self.scheduled_date)
        return (
            f"Hi {self.client_service.client.first_name}, surveyors for "
            f"{self.client_service.service.name} have been scheduled for "
            f"{scheduled_local.strftime('%A, %d %B %Y at %I:%M %p')}."
        )

    def save(self, *args, **kwargs):
        if not self.dispatch_message:
            self.dispatch_message = self.generate_default_message()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.client_service} - Scheduled: {timezone.localtime(self.scheduled_date).strftime('%Y-%m-%d %H:%M')}"




class BookingAssignment(models.Model):
    booking     = models.ForeignKey(Booking, on_delete=models.CASCADE)
    surveyor    = models.ForeignKey(
                    settings.AUTH_USER_MODEL,
                    on_delete=models.CASCADE,
                    limit_choices_to={'groups__name': 'Surveyor'}
                  )
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('booking', 'surveyor')
        ordering = ['assigned_at']

    def __str__(self):
        return f"{self.surveyor} → {self.booking}"






class ClientServiceProcess(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('collected', 'Title Deed Collected'),
    ]

    client_service = models.ForeignKey(
        'ClientService',
        related_name='service_processes',
        on_delete=models.CASCADE
    )
    process = models.ForeignKey(
        'Process',
        related_name='service_processes',
        on_delete=models.CASCADE
    )
    # ← New field to hold client‐specific cost overrides
    overridden_cost = models.DecimalField(
        max_digits=10, decimal_places=2,
        null=True, blank=True,
        help_text="If set, this cost is used instead of the template cost"
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    completed_at = models.DateTimeField(null=True, blank=True)
    # Onboarding Fields
    completed_at_onboarding = models.BooleanField(
        default=False,
        help_text="True if this process was marked complete during client onboarding"
    )
    onboarding_marked_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='onboarded_processes',
        help_text="User who marked this process as complete during onboarding"
    )
    onboarding_marked_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this process was marked complete during onboarding"
    )


    paid_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    @property
    def cost(self) -> Decimal:
        """
        The cost that should be charged for this step:
        overridden_cost if provided, else the template process.cost.
        """
        if self.overridden_cost is not None:
            return self.overridden_cost
        return self.process.cost

    @property
    def pending_amount(self) -> Decimal:
        """
        Amount still due on this step.
        """
        return self.cost - self.paid_amount

    @property
    def total_paid(self) -> Decimal:
        return self.paid_amount

    def __str__(self):
        return (
            f"{self.client_service.client.first_name} - "
            f"{self.process.name} "
            f"(Cost: {self.cost} | Paid: {self.paid_amount} | Pending: {self.pending_amount})"
        )


class ClientSubService(models.Model):
    client_service = models.ForeignKey(
        'ClientService', related_name='sub_services', on_delete=models.CASCADE
    )
    sub_service = models.ForeignKey(
        'SubService', related_name='usages', on_delete=models.CASCADE
    )
    added_on = models.DateTimeField(auto_now_add=True)
    paid_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    overridden_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
        help_text="Leave blank to use default sub-service price"
    )

    # New Fields for Payout Tracking
    is_paid_to_legal_office = models.BooleanField(default=False)
    paid_month = models.DateField(blank=True, null=True)  # e.g., 2025-05-01
    paid_at = models.DateTimeField(null=True, blank=True)
    # snapshot fields
    institution_cost_snapshot = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    overridden_price_snapshot = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    def save(self, *args, **kwargs):
        # snapshot original values only on create
        if not self.pk:
            self.institution_cost_snapshot = self.sub_service.price
            self.overridden_price_snapshot = self.overridden_price if self.overridden_price is not None else None

        # set paid_at only when marking as paid_to_legal_office
        if self.is_paid_to_legal_office and not self.paid_at:
            self.paid_at = timezone.now()

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.client_service} → {self.sub_service.name}"
    
    def clean(self):
        if self.overridden_price is not None and self.overridden_price < self.sub_service.price:
            raise ValidationError(
                f"Overridden price (Ksh {self.overridden_price}) cannot be less than base price (Ksh {self.sub_service.price})."
            )

    @property
    def price(self):
        return self.overridden_price if self.overridden_price is not None else self.sub_service.price

    @property
    def balance(self):
        return max(Decimal('0.00'), self.price - self.paid_amount)


class SubService(models.Model):
    class RoleChoices(models.TextChoices):
        LEGAL = 'Legal', 'Legal'
        OTHER = 'Other',  'Other'

    name = models.CharField(max_length=100)  # e.g. Legal stamp
    department = models.CharField(
        max_length=20,
        choices=RoleChoices.choices,
        default=RoleChoices.LEGAL
    )
    description = models.TextField(blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    date_recorded = models.DateField(default=timezone.now)



    def __str__(self):
        return f"{self.name} – KSH {self.price}"

    def month_label(self):
        return self.date_recorded.strftime('%B %Y')



class LegalOfficePayout(models.Model):
    month = models.DateField(unique=True)  # Represents the payout month
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    paid_at = models.DateTimeField(auto_now_add=True)
    subservices = models.ManyToManyField('ClientSubService', related_name='legalofficepayouts')  # Linking subservices paid for that month

    def __str__(self):
        return f"Payout for {self.month.strftime('%B %Y')} – KSH {self.total_amount}"

    def service_count(self):
        return self.subservices.count()









# Title Deed Collection Model
class TitleDeedCollection(models.Model):
    client_service = models.OneToOneField(ClientService, on_delete=models.CASCADE, related_name='title_deed_collection')
    collected_by = models.CharField(max_length=100, help_text="Name of the person who picked the title deed")
    id_number = models.CharField(max_length=20, help_text="ID number of collector", blank=True, null=True)
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    message = models.TextField(blank=True, null=True)  # <-- Add this line
    collected_at = models.DateTimeField(default=timezone.now)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, editable=False)

    def __str__(self):
        return f"{self.collected_by} collected title deed for {self.client_service.client.first_name} on {self.collected_at.strftime('%Y-%m-%d')}"




class PaymentHistory(models.Model):
    REASONS = [
        ('service_step', 'Service Process Step Payment'),
        ('sub_service', 'Sub-service Payment'),
    ]

    payment = models.ForeignKey(
        'Payment',
        null=True,  # Allow for easier migration
        blank=True,
        related_name='history',
        on_delete=models.CASCADE
    )
    client_service = models.ForeignKey(
        'ClientService',
        related_name='payment_history',
        on_delete=models.CASCADE
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.CharField(max_length=20, choices=REASONS, default='service_step')
    service_process = models.ForeignKey(
        'ClientServiceProcess',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    sub_service = models.ForeignKey(
        'ClientSubService',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['reason']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"History for {self.client_service} – {self.amount} ({self.get_reason_display()})"




class Payment(models.Model):
    PAYMENT_METHODS = [
        ('mpesa', 'M-Pesa'),
        ('cash', 'Cash'),
        ('bank_transfer', 'Bank Transfer'),
    ]

    client_service = models.ForeignKey(
        'ClientService',
        related_name='payments',
        on_delete=models.CASCADE
    )
    applied_to_subservice = models.ForeignKey(
        'ClientSubService',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        help_text="If set, apply this payment directly to the chosen client subservice"
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS)
    transaction_id = models.CharField(max_length=100, blank=True, null=True)
    payment_date = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    institution_cost_snapshot = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    overridden_total_snapshot = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="payments_received",
        null=True, blank=True,
        help_text="Staff user who received the payment"
    )

    IMMUTABLE_FIELDS = (
        'client_service_id',
        'applied_to_subservice_id',
        'amount',
        'payment_method',
        'transaction_id',
        'payment_date',
        'institution_cost_snapshot',
        'overridden_total_snapshot',
        'received_by_id',
    )

    def __str__(self):
        return (
            f"{self.client_service.client.first_name} – "
            f"KSH {self.amount} on {self.payment_date:%Y-%m-%d}"
        )


    def clean(self):
        if self.pk and not getattr(self, '_allow_mutation', False):
            previous = Payment.objects.filter(pk=self.pk).values(*self.IMMUTABLE_FIELDS).first()
            if previous:
                has_changes = any(previous[field] != getattr(self, field) for field in self.IMMUTABLE_FIELDS)
                if has_changes:
                    raise ValidationError(
                        "Payments are immutable once recorded. "
                        "Create a compensating adjustment entry instead of editing this payment."
                    )

        balance = self.client_service.total_balance
        if self.amount > balance:
            raise ValidationError(
                f"You’re trying to pay KES {self.amount:.2f}, but the remaining balance is only KES {balance:.2f}."
            )

    def save(self, *args, **kwargs):
        self._allow_mutation = bool(kwargs.pop('allow_mutation', False))
        try:
            self.clean()
            super().save(*args, **kwargs)
        finally:
            if hasattr(self, '_allow_mutation'):
                delattr(self, '_allow_mutation')

    @property
    def adjusted_amount(self) -> Decimal:
        total = self.adjustments.aggregate(
            total=Coalesce(Sum('amount'), Value(0), output_field=DecimalField())
        )['total']
        return Decimal(total or 0)

    @property
    def remaining_adjustable(self) -> Decimal:
        return max(Decimal('0.00'), Decimal(self.amount or 0) - self.adjusted_amount)

    @property
    def is_fully_adjusted(self) -> bool:
        return self.remaining_adjustable <= Decimal('0.00')


class PaymentAdjustment(models.Model):
    """
    Admin-only compensating entry for an existing Payment.
    - Preserves the original Payment record (immutable ledger).
    - On creation, triggers a Cash OUT in the cashbook (clawback).
    - Marked by the staff member who authorised the correction.
    """
    ADJUSTMENT_TYPES = [
        ('reversal', 'Full Reversal'),
        ('partial', 'Partial Adjustment'),
    ]

    original_payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        related_name='adjustments',
        help_text="The original payment being reversed or adjusted"
    )
    adjustment_type = models.CharField(
        max_length=20, choices=ADJUSTMENT_TYPES, default='reversal'
    )
    amount = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Positive claw-back amount (posted as Cash OUT in cashbook)"
    )
    reason = models.TextField(
        help_text="Mandatory audit reason for this adjustment"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='payment_adjustments_created',
        help_text="Staff user who authorised this adjustment"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Payment Adjustment'
        verbose_name_plural = 'Payment Adjustments'

    def __str__(self):
        return (
            f"{self.get_adjustment_type_display()} – KES {self.amount} "
            f"on Payment #{self.original_payment_id} by {self.created_by}"
        )

    def clean(self):
        from decimal import Decimal as _D
        if self.amount is None or self.amount <= 0:
            raise ValidationError("Adjustment amount must be positive.")

        if not getattr(self, 'reason', None) or not str(self.reason).strip():
            raise ValidationError("A reason is required for payment adjustments.")

        if self.original_payment_id:
            already_adjusted = (
                PaymentAdjustment.objects
                .filter(original_payment_id=self.original_payment_id)
                .exclude(pk=self.pk)
                .aggregate(total=models.Sum('amount'))['total'] or _D('0.00')
            )
            max_adjustable = self.original_payment.amount
            if already_adjusted + self.amount > max_adjustable:
                raise ValidationError(
                    f"Total adjustments ({already_adjusted + self.amount:.2f}) cannot exceed "
                    f"original payment amount ({max_adjustable:.2f})."
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class SubServicePaymentAdjustment(models.Model):
    """
    Admin/staff compensating entry for ClientSubService paid amount.
    - Preserves append-only audit history for sub-service corrections.
    - Posts a compensating Cash OUT in the cashbook.
    - Writes negative PaymentHistory('sub_service') entries via signal.
    """
    ADJUSTMENT_TYPES = [
        ('reversal', 'Full Reversal'),
        ('partial', 'Partial Adjustment'),
    ]

    client_sub_service = models.ForeignKey(
        'ClientSubService',
        on_delete=models.PROTECT,
        related_name='payment_adjustments',
        help_text="The client sub-service whose paid amount is being corrected"
    )
    adjustment_type = models.CharField(
        max_length=20, choices=ADJUSTMENT_TYPES, default='reversal'
    )
    amount = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Positive claw-back amount (posted as Cash OUT in cashbook)"
    )
    reason = models.TextField(
        help_text="Mandatory audit reason for this adjustment"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='subservice_payment_adjustments_created',
        help_text="Staff user who authorised this adjustment"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Sub-service Payment Adjustment'
        verbose_name_plural = 'Sub-service Payment Adjustments'

    def __str__(self):
        return (
            f"{self.get_adjustment_type_display()} – KES {self.amount} "
            f"on SubService #{self.client_sub_service_id} by {self.created_by}"
        )

    def clean(self):
        from decimal import Decimal as _D
        if self.pk:
            raise ValidationError("Sub-service payment adjustments are immutable once recorded.")

        if self.amount is None or self.amount <= 0:
            raise ValidationError("Adjustment amount must be positive.")

        if not getattr(self, 'reason', None) or not str(self.reason).strip():
            raise ValidationError("A reason is required for sub-service payment adjustments.")

        if self.client_sub_service_id:
            current_paid = _D(self.client_sub_service.paid_amount or _D('0.00'))
            if self.amount > current_paid:
                raise ValidationError(
                    f"Adjustment amount ({self.amount:.2f}) cannot exceed currently paid amount "
                    f"({current_paid:.2f}) for this sub-service."
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


# your_app/models.py
class Expense(models.Model):
    date = models.DateField(default=timezone.localdate)
    description = models.CharField(max_length=255, blank=True)
    amount      = models.DecimalField(max_digits=10, decimal_places=2)
    payment_mode= models.CharField(max_length=32, choices=[
                    ('mpesa','M-Pesa'),
                    ('cash','Cash'),
                    ('bank','Bank Transfer'),
                  ])
    recorded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='expenses_recorded')
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='expenses_approved')
    receipt_no  = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ['-date']
        verbose_name = 'Expense'
        verbose_name_plural = 'Expenses'

    def __str__(self):
        return f"{self.description} — {self.amount} on {self.date}"




# -----------------------
# Office Document
# -----------------------
class DocumentManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().select_related('doc_type', 'uploaded_by')
    
    def by_type(self, doc_type_name):
        return self.filter(doc_type__name=doc_type_name)
    
    def with_drive_access(self):
        return self.filter(
            models.Q(storage_backend__in=['drive', 'hybrid']) |
            models.Q(drive_file_id__isnull=False)
        )
    
    def recent_uploads(self, days=30):
        cutoff = timezone.now() - timezone.timedelta(days=days)
        return self.filter(uploaded_at__gte=cutoff)

class BaseDocument(models.Model):
    from apps.EasyDocs.files.storage_backends import UnifiedStorage

    UPLOAD_STATUS = [
        ('pending', 'Pending'),
        ('uploaded', 'Uploaded'),
        ('local', 'Local Only'),
        ('failed', 'Upload Failed'),
    ]

    STORAGE_BACKEND_CHOICES = [
        ('local', 'Local Storage'),
        ('drive', 'Google Drive'),
        ('hybrid', 'Both Local and Drive'),
    ]

    doc_name = models.CharField(max_length=255)

    doc_file = models.FileField(
        upload_to="documents/",  
        validators=[validate_file_size],
    )

    doc_type = models.ForeignKey(DocType, on_delete=models.PROTECT)
    uploaded_by = models.ForeignKey(User, on_delete=models.PROTECT)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    # Storage tracking
    storage_backend = models.CharField(
        max_length=20, 
        choices=STORAGE_BACKEND_CHOICES, 
        default='local'
    )
    drive_file_id = models.CharField(max_length=255, blank=True, null=True)
    drive_url = models.URLField(blank=True, null=True)
    local_path = models.CharField(max_length=500, blank=True, null=True)

    status = models.CharField(max_length=20, choices=UPLOAD_STATUS, default='pending')
    failure_reason = models.TextField(blank=True, null=True)

    objects = DocumentManager()

    class Meta:
        abstract = True

    def __str__(self):
        return self.doc_name

    @property
    def site_settings(self):
        from .models import SiteSettings
        return SiteSettings.objects.first()

    def get_drive_folder_path(self):
        """Generate consistent Drive folder path based on document type"""
        if isinstance(self, Document):
            return f"office/{self.doc_type.name}/{self.uploaded_at.year}/{self.uploaded_at.month:02d}"
        return f"clients/{self.client.id}/{self.doc_type.name}/{self.uploaded_at.year}/{self.uploaded_at.month:02d}"

    def get_drive_file_name(self):
        """Generate Drive file name with timestamp to avoid conflicts"""
        timestamp = self.uploaded_at.strftime('%Y%m%d_%H%M%S')
        return f"{timestamp}_{self.doc_name}"

    def get_full_drive_path(self):
        return f"{self.get_drive_folder_path()}/{self.get_drive_file_name()}"

    # ✅ Refactored for Option 2
    @property
    def file_available(self):
        """Check if file exists in the correct backend."""
        from apps.EasyDocs.files.storage_backends import UnifiedStorage
        storage = UnifiedStorage()

        if not self.doc_file or not self.doc_file.name:
            return False

        if self.storage_backend in ("local", "hybrid"):
            return storage._local_exists(self.doc_file.name)
        elif self.storage_backend == "drive":
            return storage._drive_exists(self.doc_file.name)

        return False

    def get_file_content(self):
        """Open the file content from the proper backend."""
        from apps.EasyDocs.files.storage_backends import UnifiedStorage
        storage = UnifiedStorage()
        try:
            with storage.open(self) as f:
                return f.read()
        except Exception as e:
            logger.error(f"❌ Failed to fetch content for {self.id}: {e}")
            return None
        
    def file_url(self):
        try:
            from apps.EasyDocs.files.storage_backends import UnifiedStorage
            storage = UnifiedStorage()

            if self.storage_backend == "drive":
                if self.drive_file_id:
                    return storage.url(self.drive_file_id, backend="drive")
                return self.drive_url or None

            # local or hybrid (prefer doc_file if present)
            if self.doc_file and getattr(self.doc_file, 'name', None):
                return storage.url(self.doc_file.name, backend=self.storage_backend)

            return self.drive_url or None
        except Exception as e:
            logger.warning("file_url resolution failed for %s: %s", getattr(self, 'id', None), e)
            return None


class Document(BaseDocument):
    location = models.CharField(max_length=100, default="Office")
    reference = models.CharField(max_length=100, default="AUTO")
    
    class Meta:
        verbose_name = "Office Document"
        verbose_name_plural = "Office Documents"
    
    def save(self, *args, **kwargs):
        # ✅ Don’t override doc_file.name — paths are handled by upload_document_with_strategy
        super().save(*args, **kwargs)

class ClientDoc(BaseDocument):
    client = models.ForeignKey('Client', on_delete=models.CASCADE, related_name='documents')
    
    class Meta:
        verbose_name = "Client Document"
        verbose_name_plural = "Client Documents"
    
    def save(self, *args, **kwargs):
        # ✅ Don’t override doc_file.name — paths are handled by upload_document_with_strategy
        super().save(*args, **kwargs)



# SMS Provider Token Model
class SmsProviderToken(models.Model):
    api_token = models.CharField(max_length=255)
    sender_id = models.CharField(max_length=255)
    singleton_enforcer = models.BooleanField(default=True, unique=True)

    def __str__(self):
        return f"Token: {self.sender_id}"

    def save(self, *args, **kwargs):
        self.singleton_enforcer = True  # enforce only one row
        super().save(*args, **kwargs)
        cache.delete('sms_provider_token')



from django.db import models
from django.utils import timezone
import uuid
from django.db import models
from django.utils import timezone

class ScheduledTask(models.Model):
    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("sent", "Sent"),
        ("cancelled", "Cancelled"),
        ("failed", "Failed"),
    )
    
    TASK_TYPE_CHOICES = (
        ("reminder", "Reminder"),
        ("scheduled_sms", "Scheduled SMS"),
        ("notification", "Notification"),
        ("other", "Other"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task_id = models.CharField(max_length=255, unique=True)
    task_name = models.CharField(max_length=255)
    task_type = models.CharField(
        max_length=20,
        choices=TASK_TYPE_CHOICES,
        default="other",
        help_text="Type of scheduled task",
        db_index=True,
    )
    scheduled_time = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    message_preview = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    payload = models.JSONField(blank=True, null=True)
    
    # Reminder-specific fields
    client_service = models.ForeignKey(
        'ClientService',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='scheduled_tasks',
        help_text="Related client service (for reminders)",
    )
    assigned_employee = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='scheduled_tasks',
        help_text="Employee assigned to this task (for reminders)",
    )
    notes = models.TextField(
        blank=True,
        help_text="Additional notes or context for the task",
    )

    def is_cancelable(self):
        return self.status == "pending" and self.scheduled_time > timezone.now()

    def __str__(self):
        return f"{self.task_name} scheduled at {self.scheduled_time}"
    
    class Meta:
        indexes = [
            models.Index(fields=['task_type', 'status', 'scheduled_time']),
            models.Index(fields=['assigned_employee', 'status', 'scheduled_time']),
            models.Index(fields=['client_service', 'status']),
        ]



class MessageLog(models.Model):
    RECIPIENT_CHOICES = [
        ('client', 'Client'),
        ('employee', 'Employee'),
        ('company', 'Company'),
    ]

    client_service = models.ForeignKey(
        'ClientService',
        on_delete=models.CASCADE,
        related_name='message_logs',
        null=True,
        blank=True,
    )
    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    phone = models.CharField(max_length=20)
    message = models.TextField()
    reason = models.CharField(max_length=255)
    recipient_type = models.CharField(max_length=20, choices=RECIPIENT_CHOICES, default='client')
    message_id = models.CharField(max_length=255, blank=True, null=True)
    is_company_copy = models.BooleanField(default=False, db_index=True)
    send_status = models.CharField(
        max_length=20,
        choices=[('sent', 'Sent'), ('failed', 'Failed')]
    )
    delivery_status = models.CharField(
        max_length=20,
        choices=[('pending', 'Pending'), ('delivered', 'Delivered'), ('failed', 'Failed')],
        default='pending'
    )
    error_details = models.TextField(blank=True, null=True)

    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['reason', 'is_company_copy'],
                condition=models.Q(is_company_copy=True),
                name='unique_company_copy_per_reason'
            )
        ]
        indexes = [
            
            # 1️⃣ Main DLR poller
            models.Index(
                fields=['delivery_status', 'timestamp'],
                name='msglog_delivery_ts_idx'
            ),

            # 2️⃣ Partial index (Postgres only)
            models.Index(
                fields=['timestamp'],
                name='msglog_pending_msg_id_idx',
                condition=Q(
                    delivery_status='pending',
                    message_id__isnull=False
                )
            ),

            # 3️⃣ Pagination
            models.Index(
                fields=['-timestamp'],
                name='msglog_time_desc_idx'
            ),

            # 4️⃣ Aggregations
            models.Index(
                fields=['send_status'],
                name='msglog_send_status_idx'
            ),

            # 5️⃣ Message lookup
            models.Index(
                fields=['message_id'],
                name='msglog_message_id_idx'
            ),
        ]

    def __str__(self):
        client_repr = str(self.client) if self.client else f"(no client) {self.recipient_type}"
        return f"{client_repr} | {self.reason} | {self.send_status}"
   
    
    
    
    
    


# models.py (append at bottom)

class AuditLog(models.Model):
    ACTION_CHOICES = [
        ('upload', 'Upload'),
        ('download', 'Download'),
        ('delete', 'Delete'),
        ('email_sent', 'Email Sent'),
        ('email_failed', 'Email Failed'),
        ('drive_sync', 'Drive Sync'),
        ('drive_failed', 'Drive Sync Failed'),
        ('payment', 'Payment Made'),
        ('payment_failed', 'Payment Failed'),
        ('expense_recorded', 'Expense Recorded'),
        ('expense_failed', 'Expense Recording Failed'),
        ('document_accessed', 'Document Accessed'),
        ('document_modified', 'Document Modified'),
        ('login', 'User Login'),
        ('logout', 'User Logout'),
        ('data_export', 'Data Exported'),
        ('data_import', 'Data Imported'),
        ('config_change', 'Configuration Changed'),
        ('other', 'Other'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    model_name = models.CharField(max_length=100)
    object_id = models.CharField(max_length=255)  # Can store integers, UUIDs, strings
    description = models.TextField(blank=True, null=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.TextField(blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.action} - {self.model_name} {self.object_id}"

# Service Assignment & Task Management Models
# -----------------------

class ServiceAssignmentLog(models.Model):
    """
    Audit trail for service assignments, acceptances, declines, and reassignments.
    """
    ACTION_CHOICES = [
        ('assigned', 'Assigned'),
        ('accepted', 'Accepted'),
        ('declined', 'Declined'),
        ('reassigned', 'Reassigned'),
    ]
    
    client_service = models.ForeignKey(
        'ClientService',
        on_delete=models.CASCADE,
        related_name='assignment_logs',
        help_text="The service that was assigned/reassigned"
    )
    assigned_employee = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='received_assignments',
        help_text="Employee who received this assignment"
    )
    previous_employee = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='previous_assignments',
        help_text="Previous employee (for reassignments)"
    )
    action = models.CharField(
        max_length=20,
        choices=ACTION_CHOICES,
        help_text="Action taken"
    )
    assigned_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='assignments_created',
        help_text="User who made the assignment"
    )
    timestamp = models.DateTimeField(auto_now_add=True)
    task_progress_at_reassignment = models.TextField(
        blank=True,
        help_text="JSON or text describing the task state at reassignment"
    )
    reason = models.TextField(
        blank=True,
        help_text="Reason for assignment/reassignment/decline"
    )
    
    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['client_service', '-timestamp']),
            models.Index(fields=['assigned_employee', '-timestamp']),
        ]
    
    def __str__(self):
        return f"{self.action} - {self.client_service} - {self.timestamp.strftime('%Y-%m-%d %H:%M')}"


class ServiceDeadlineExtension(models.Model):
    """
    Track deadline extensions for services.
    """
    client_service = models.ForeignKey(
        'ClientService',
        on_delete=models.CASCADE,
        related_name='deadline_extensions',
        help_text="Service whose deadline was extended"
    )
    old_deadline = models.DateTimeField(help_text="Previous deadline")
    new_deadline = models.DateTimeField(help_text="New deadline after extension")
    extended_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='deadline_extensions_created',
        help_text="User who extended the deadline"
    )
    extended_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField(help_text="Reason for deadline extension")
    
    class Meta:
        ordering = ['-extended_at']
        indexes = [
            models.Index(fields=['client_service', '-extended_at']),
        ]
    
    def __str__(self):
        return f"{self.client_service} - Extended by {self.extended_by} on {self.extended_at.strftime('%Y-%m-%d')}"


# Document Handoff Models
# -----------------------

class DocumentHandoff(models.Model):
    """
    Track document assignments and handoffs to employees.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('declined', 'Declined'),
        ('reassigned', 'Reassigned'),
    ]
    
    # Generic relation to support both ClientDoc and Document
    from django.contrib.contenttypes.fields import GenericForeignKey
    from django.contrib.contenttypes.models import ContentType
    
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    document = GenericForeignKey('content_type', 'object_id')
    
    client = models.ForeignKey(
        'Client',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='document_handoffs',
        help_text="Client (if this is a ClientDoc)"
    )
    assigned_to = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='received_document_handoffs',
        help_text="Employee to whom the document is assigned"
    )
    assigned_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='created_document_handoffs',
        help_text="User who assigned the document"
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        help_text="Current handoff status"
    )
    accepted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the employee accepted the document"
    )
    max_acceptance_time = models.DateTimeField(
        help_text="Maximum time for acceptance (assigned_at + 1 day)"
    )
    notes = models.TextField(
        blank=True,
        help_text="Additional notes about the handoff"
    )
    
    class Meta:
        ordering = ['-assigned_at']
        indexes = [
            models.Index(fields=['assigned_to', 'status', '-assigned_at']),
            models.Index(fields=['client', '-assigned_at']),
        ]
    
    def save(self, *args, **kwargs):
        # Auto-calculate max_acceptance_time if not set
        if not self.max_acceptance_time and self.assigned_at:
            from datetime import timedelta
            self.max_acceptance_time = self.assigned_at + timedelta(days=1)
        elif not self.max_acceptance_time:
            from datetime import timedelta
            self.max_acceptance_time = timezone.now() + timedelta(days=1)
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"Document handoff to {self.assigned_to.get_full_name() or self.assigned_to.username} - {self.status}"


class DocumentHandoffLog(models.Model):
    """
    Audit trail for document handoff actions.
    """
    ACTION_CHOICES = [
        ('assigned', 'Assigned'),
        ('accepted', 'Accepted'),
        ('declined', 'Declined'),
        ('reassigned', 'Reassigned'),
    ]
    
    handoff = models.ForeignKey(
        'DocumentHandoff',
        on_delete=models.CASCADE,
        related_name='logs',
        help_text="The handoff this log entry is for"
    )
    action = models.CharField(
        max_length=20,
        choices=ACTION_CHOICES,
        help_text="Action taken"
    )
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='document_handoff_actions',
        help_text="User who performed the action"
    )
    timestamp = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(
        blank=True,
        help_text="Additional notes about the action"
    )
    
    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['handoff', '-timestamp']),
        ]
    
    def __str__(self):
        return f"{self.action} - {self.handoff} - {self.timestamp.strftime('%Y-%m-%d %H:%M')}"



