from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import models
from django.db.models import Sum
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils import timezone
from django.core.cache import cache
from apps.EasyDocs.utils import MobileSasaAPI

# Gender choices
class Gender(models.TextChoices):
    MALE = 'Male', 'Male'
    FEMALE = 'Female', 'Female'
    OTHER = 'Others', 'Others'

class ServiceCategory(models.TextChoices):
    TITLE   = 'title',   'Title Deed Service'
    GROUND  = 'ground',  'Ground Service'
    # …future categories here




# models.py

class EmailSettings(models.Model):
    singleton_enforcer = models.BooleanField(default=True, editable=False, unique=True)

    email_host = models.CharField(max_length=255, blank=True, null=True)
    email_port = models.PositiveIntegerField(default=587)
    email_host_user = models.CharField(max_length=255, blank=True, null=True)
    email_host_password = models.CharField(max_length=255, blank=True, null=True)
    default_from_email = models.EmailField(validators=[validate_email], blank=True, null=True)

    def __str__(self):
        return "Email Settings"




class SiteSettings(models.Model):
    # Enforce only one row
    singleton_enforcer = models.BooleanField(default=True, editable=False, unique=True)

    company_name    = models.CharField(max_length=200, default="GREAT GUARDIAN")
    logo            = models.ImageField(upload_to="company/", blank=True, null=True)
    email           = models.EmailField(validators=[validate_email], default="info@example.com")
    phone           = models.CharField(max_length=20, blank=True, null=True)
    tagline         = models.CharField(max_length=255, blank=True, default="Thank you for letting us serve you!")
    stamp_signature = models.ImageField(upload_to="company/", blank=True, null=True)

    def __str__(self):
        return "Site Settings"

    class Meta:
        verbose_name = "Site Settings"
        verbose_name_plural = "Site Settings"



# Document Type
class DocType(models.Model):
    name = models.CharField(max_length=100, help_text="Enter document type name")

    def __str__(self):
        return self.name

# Office Documents
class Document(models.Model):
    doc_name = models.CharField(max_length=100, help_text="Enter a short name for the document")
    doc_type = models.ForeignKey(DocType, on_delete=models.CASCADE, related_name='documents')
    location = models.CharField(max_length=50)
    reference = models.CharField(max_length=50)
    file = models.FileField(upload_to='office_documents/', blank=True, null=True, help_text="Upload office-related documents")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['doc_name']),
            models.Index(fields=['location']),
            models.Index(fields=['uploaded_at']),
        ]

    def __str__(self):
        return f"{self.doc_name} - {self.reference} ({self.location})"

# Client Model
class Client(models.Model):
    first_name = models.CharField(max_length=100,db_index=True)
    last_name = models.CharField(max_length=100,db_index=True)
    email = models.EmailField(unique=True, blank=True, null=True,db_index=True)
    phone = models.CharField(max_length=15,db_index=True)

    def __str__(self):
        return self.first_name

# Service Model
class Service(models.Model):
    CATEGORY_CHOICES = ServiceCategory.choices

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # NEW FIELDS
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default=ServiceCategory.TITLE)
    dispatch_message = models.TextField(blank=True, null=True, help_text="Message to send for dispatch-based services.")

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
    client = models.ForeignKey(Client, related_name='client_services', on_delete=models.CASCADE)
    service = models.ForeignKey(Service, related_name='client_services', on_delete=models.CASCADE)
    land_description = models.CharField(max_length=255)
    requested_at = models.DateTimeField(auto_now_add=True)
    overridden_total_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
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
                condition=models.Q(status='active'),
                name='unique_active_service_per_land'
            )
        ]

    @property
    def current_status(self):
        if hasattr(self, 'title_deed_collection'):
            return 'collected'
        return self.status

    def total_paid(self):
        return self.payments.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    @property
    def processes_total(self):
        """
        Sum of all per-client process costs, using overridden_cost if set.
        """
        agg = self.service_processes.aggregate(
            total=Sum(
                # Use Coalesce to treat overridden_cost if present, else process.cost
                models.F('overridden_cost'),
                output_field=models.DecimalField()
            )
        )['total']
        # However F('overridden_cost') will be null for many rows, so fallback:
        if agg is None:
            # compute in Python
            return sum((csp.cost for csp in self.service_processes.all()), Decimal('0.00'))
        return agg

    @property
    def sub_services_total(self) -> Decimal:
        total = Decimal('0.00')
        for css in self.sub_services.all():
            total += css.price
        return total

    @property
    def full_total_price(self):
        """
        Grand total: processes + sub-services.
        """
        return self.processes_total + self.sub_services_total

    @property
    def effective_total_price(self) -> Decimal:
        """
        • If the service has NO processes (dispatch-only):
            – If overridden_total_price is set on this client service, use that.
            – Otherwise fall back to Service.total_price.
        • If the service HAS processes (process-based):
            – Sum each ClientServiceProcess.cost (overridden_cost or template cost).
            – Then add each ClientSubService.price.
        """
        # 1️⃣ Dispatch-only
        if not self.service.processes.exists():
            base = (
                self.overridden_total_price
                if self.overridden_total_price is not None
                else self.service.total_price
            )
            return base + self.sub_services_total

            # Process‑based
        total = Decimal('0.00')
        for csp in self.service_processes.all():
            step_cost = (
                csp.overridden_cost
                if csp.overridden_cost is not None
                else csp.process.cost
            )
            total += step_cost

        total += self.sub_services_total
        return total

    def total_balance(self):
        """
        Amount still owed: full total minus what’s been paid.
        """
        return self.effective_total_price -self.total_paid()

    @property
    def payment_status(self):
        balance = self.total_balance()
        if balance <= 0:
            return 'Fully Paid'
        elif self.total_paid() > 0:
            return 'Partially Paid'
        else:
            return 'Not Paid'

    def __str__(self):
        return f" {self.service.name} for {self.land_description}"

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

    def __str__(self):
        return f"{self.client_service} → {self.sub_service.name}"

    @property
    def price(self):
        return self.overridden_price if self.overridden_price is not None else self.sub_service.price

    @property
    def balance(self):
        return max(Decimal('0.00'), self.price - self.paid_amount)


class SubService(models.Model):
    name = models.CharField(max_length=100)
    department = models.CharField(max_length=100)  # e.g. Legal Department
    description = models.TextField(blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.name} – KSH {self.price}"







# Title Deed Collection Model
class TitleDeedCollection(models.Model):
    client_service = models.OneToOneField(ClientService, related_name='title_deed_collection', on_delete=models.CASCADE)
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
    reason = models.CharField(max_length=20, choices=REASONS,default='payment_step')
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
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS)
    transaction_id = models.CharField(max_length=100, blank=True, null=True)
    payment_date = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return (
            f"{self.client_service.client.first_name} – "
            f"KSH {self.amount} on {self.payment_date:%Y-%m-%d}"
        )

    def clean(self):
        balance = self.client_service.total_balance()
        if self.amount > balance:
            raise ValidationError(
                f"You’re trying to pay KES {self.amount:.2f}, but the remaining balance is only KES {balance:.2f}."
            )


    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)





# your_app/models.py
class Expense(models.Model):
    date= models.DateField(auto_now_add=True)
    description = models.CharField(max_length=255, blank=True)
    amount      = models.DecimalField(max_digits=10, decimal_places=2)
    payment_mode= models.CharField(max_length=32, choices=[
                    ('mpesa','M-Pesa'),
                    ('cash','Cash'),
                    ('bank','Bank Transfer'),
                  ])
    handled_by  = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='expenses_handled')
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='expenses_approved')
    receipt_no  = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ['-date']
        verbose_name = 'Expense'
        verbose_name_plural = 'Expenses'

    def __str__(self):
        return f"{self.description} — {self.amount} on {self.date}"







class ClientDoc(models.Model):
    client = models.ForeignKey(Client, related_name='client_documents', on_delete=models.CASCADE)
    doc_name = models.CharField(max_length=100)
    doc_type = models.ForeignKey('DocType', on_delete=models.CASCADE, related_name='client_doc')
    doc_file = models.FileField(upload_to='client_docs/')
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, editable=False)

    def __str__(self):
        return f"{self.doc_name} for {self.client.first_name}"





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
