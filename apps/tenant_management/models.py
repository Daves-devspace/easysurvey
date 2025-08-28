
from django.db import models
from django.utils import timezone
from django.db.models import F, Index, UniqueConstraint
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db.models import Q, Prefetch


class Property(models.Model):
    """
    Represents a physical property or plot, with configurable water billing policy
    and rate. Contains metadata like name and location.
    """
    SHARED = 'shared'
    METER = 'meter'
    PREPAID = 'prepaid'
    WATER_POLICY_CHOICES = [
        (SHARED, 'Shared'),
        (METER, 'Per Meter'),
        (PREPAID, 'Prepaid'),
    ]

    name = models.CharField(
        max_length=100,
        db_index=True,
        help_text="The display name of the property."
    )
    location = models.CharField(
        max_length=255,
        help_text="Physical address or description of the property location."
    )
    water_policy = models.CharField(
        max_length=50,
        choices=WATER_POLICY_CHOICES,
        default=SHARED,
        db_index=True,
        help_text="Defines how water billing is handled: shared, metered, or prepaid."
    )
    water_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.0,
        help_text="Cost per cubic meter of water for metered properties (Ksh)."
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when the property record was created."
    )

    class Meta:
        indexes = [
            Index(fields=['water_policy']),  # Speeds up queries filtering by policy
        ]

    def __str__(self):
        """String representation of the Property."""
        return self.name


class Unit(models.Model):
    """
    Represents an individual rentable unit (e.g., apartment, room) within a property.
    Includes rent amount and meter number if applicable.
    """
    property = models.ForeignKey(
        Property,
        on_delete=models.CASCADE,
        related_name='units',
        help_text="The parent property this unit belongs to."
    )
    unit_number = models.CharField(
        max_length=50,
        help_text="Identifier for the unit within the property (e.g., A1, B12)."
    )
    rent_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Monthly rent amount for this unit (Ksh)."
    )
    is_occupied = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Indicates whether the unit is currently occupied by a tenant."
    )
    meter_number = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text="Identifier for the water meter, if this unit uses metered billing."
    )

    class Meta:
        unique_together = ('property', 'unit_number')  # Prevent duplicate unit numbers
        indexes = [
            Index(fields=['property', 'unit_number']),
        ]

    def __str__(self):
        """String representation combining property name and unit number."""
        return f"{self.property.name} - {self.unit_number}"
    
    # @property
    # def tenant_name(self):
    #     if self.is_occupied:
    #         # signals guarantee a lease exists
    #         return self.lease.tenant.full_name
    #     return "Vacant"


class Tenant(models.Model):
    """
    Stores tenant personal details and contact information.
    Each tenant belongs to a specific property and can have multiple leases
    within that property over time.
    """
    property = models.ForeignKey(
        Property,
        on_delete=models.CASCADE,
        related_name="tenants",
        help_text="The property this tenant belongs to."
    )
    full_name = models.CharField(
        max_length=100,
        help_text="Tenant's full legal name."
    )
    phone_number = models.CharField(
        max_length=15,
        help_text="Tenant's primary contact phone number (unique per property)."
    )
    email = models.EmailField(
        blank=True,
        null=True,
        help_text="Optional email for notifications and records."
    )
    national_id = models.CharField(
        max_length=20,
        help_text="Official government-issued identification number."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # ensures two different properties can each have a tenant with the same phone or ID
        constraints = [
            UniqueConstraint(fields=['property', 'phone_number'], name='unique_tenant_phone_per_property'),
            UniqueConstraint(fields=['property', 'national_id'], name='unique_tenant_id_per_property'),
        ]
        indexes = [
            Index(fields=['property', 'phone_number']),
            Index(fields=['property', 'national_id']),
        ]

    def __str__(self):
        return f"{self.full_name} ({self.property.name})"



class Lease(models.Model):
    """
    Links a Tenant to a Unit for a rental period.
    Handles deposit tracking and active/inactive status.
    """
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name='leases',
        help_text="The tenant taking the lease."
    )
    unit = models.OneToOneField(
        Unit,
        on_delete=models.CASCADE,
        help_text="The specific unit leased by the tenant."
    )
    start_date = models.DateField(
        help_text="Lease commencement date."
    )
    deposit_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Security deposit held for the lease."
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Indicates whether the lease is currently active."
    )

    class Meta:
        indexes = [
            Index(fields=['start_date']),
            Index(fields=['is_active']),
        ]

    def __str__(self):
        """String representation combining tenant and unit info."""
        return f"Lease: {self.tenant.full_name} -> {self.unit}"

    def end_lease(self):
        """
        Marks the lease as inactive (e.g., tenant moved out).
        """
        self.is_active = False
        self.save()

    @classmethod
    def get_active_leases(cls):
        """
        Returns a queryset of all active leases.
        """
        return cls.objects.filter(is_active=True)
    
    def clean(self):
        if self.tenant.property != self.unit.property:
            raise ValidationError("Tenant and Unit must belong to the same property.")


class MeterReading(models.Model):
    """
    Records a water meter reading for a unit and calculates usage & cost.
    Automatically computes 'usage' and 'amount' on save.
    """
    unit = models.ForeignKey(
        Unit,
        on_delete=models.CASCADE,
        related_name='meter_readings',
        help_text="Unit associated with this meter reading."
    )
    reading_date = models.DateField(
        auto_now_add=True,
        db_index=True,
        help_text="Date when the meter was read."
    )
    previous_reading = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Last recorded meter value."
    )
    current_reading = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="New meter value entered by user."
    )
    usage = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        editable=False,
        help_text="Calculated usage in cubic meters."
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        editable=False,
        help_text="Calculated cost for the usage at the unit's property rate."
    )

    class Meta:
        get_latest_by = 'reading_date'
        indexes = [
            Index(fields=['unit', 'reading_date']),
        ]

    def save(self, *args, **kwargs):
        """
        Compute 'usage' and 'amount' before saving the reading.
        'amount' = usage * property's water_rate.
        """
        self.usage = self.current_reading - self.previous_reading
        self.amount = self.usage * self.unit.property.water_rate
        super().save(*args, **kwargs)

    def __str__(self):
        """String showing unit, date, and usage."""
        return f"{self.unit} @ {self.reading_date}: {self.usage} m³"


class Invoice(models.Model):
    """
    Represents a billing record for a lease period, including rent,
    water charges, and other fees. Tracks payment status.
    """
    lease = models.ForeignKey(
        Lease,
        on_delete=models.CASCADE,
        related_name='invoices',
        help_text="Lease this invoice is billed against."
    )
    invoice_date = models.DateField(
        db_index=True,
        help_text="Date invoice was generated or issued."
    )
    due_date = models.DateField(
        db_index=True,
        help_text="Date by which payment is due."
    )
    rent_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Rent portion of the invoice."
    )
    water_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.0,
        help_text="Water portion based on meter readings or shared policy."
    )
    other_charges = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.0,
        help_text="Any additional fees or fines."
    )
    total_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Sum of rent, water, and other charges."
    )
    is_paid = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Indicates if invoice is fully paid."
    )
    auto_generated = models.BooleanField(
        default=True,
        help_text="Marks invoices created automatically by system vs manual."
    )

    class Meta:
        indexes = [
            Index(fields=['lease', 'invoice_date']),
            Index(fields=['is_paid']),
        ]

    def __str__(self):
        """String representation showing invoice ID and lease."""
        return f"Invoice #{self.id} - {self.lease}"

    def mark_paid(self):
        """
        Marks the invoice as paid and saves.
        """
        self.is_paid = True
        self.save()

    @property
    def total_paid(self):
        """
        Sum of all payments made toward this invoice.
        """
        return sum(p.amount for p in self.payments.all())

    @property
    def balance(self):
        """
        Calculates remaining due amount after payments.
        """
        return self.total_amount - self.total_paid


class Payment(models.Model):
    """
    Records payments made against an invoice, often via M-Pesa.
    Automatically generates a receipt and updates invoice status.
    """
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name='payments',
        help_text="Invoice this payment applies to."
    )
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        help_text="The tenant making the payment."
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Amount paid."
    )
    payment_date = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="Timestamp when payment was recorded."
    )
    method = models.CharField(
        max_length=50,
        default='Mpesa',
        help_text="Payment method used."
    )
    mpesa_receipt = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="Reference code from M-Pesa."
    )
    balance_after = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.0,
        help_text="Remaining invoice balance after this payment."
    )

    class Meta:
        indexes = [
            Index(fields=['tenant', 'payment_date']),
        ]

    def __str__(self):
        """String summarizing payment details."""
        return f"Payment {self.amount} for Invoice {self.invoice.id}"

    def save(self, *args, **kwargs):
        """
        After saving a payment, auto-generate its receipt and update invoice status
        if fully paid.
        """
        super().save(*args, **kwargs)
        # Create a receipt record
        Receipt.objects.create(payment=self)
        # Update invoice if balance is cleared
        if self.invoice.balance <= 0:
            self.invoice.mark_paid()


class Receipt(models.Model):
    """
    Represents a generated receipt for a payment. Contains unique number and timestamp.
    """
    payment = models.OneToOneField(
        Payment,
        on_delete=models.CASCADE,
        help_text="The payment this receipt is issued for."
    )
    receipt_number = models.CharField(
        max_length=50,
        unique=True,
        help_text="Unique identifier for this receipt."
    )
    issued_date = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when receipt was created."
    )

    class Meta:
        indexes = [
            Index(fields=['receipt_number']),
        ]

    def __str__(self):
        """String representation of the receipt."""
        return f"Receipt #{self.receipt_number}"


class NotificationLog(models.Model):
    """
    Logs outgoing notifications (SMS, Email, WhatsApp) sent to tenants.
    Tracks status for auditing and troubleshooting.
    """
    SMS = 'SMS'
    EMAIL = 'EMAIL'
    WHATSAPP = 'WHATSAPP'
    CHANNEL_CHOICES = [
        (SMS, 'SMS'),
        (EMAIL, 'Email'),
        (WHATSAPP, 'WhatsApp'),
    ]

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        help_text="Recipient tenant of the notification."
    )
    message = models.TextField(
        help_text="Content of the sent notification."
    )
    channel = models.CharField(
        max_length=10,
        choices=CHANNEL_CHOICES,
        help_text="Delivery channel used for this notification."
    )
    status = models.CharField(
        max_length=20,
        default='sent',
        help_text="Delivery status (sent, failed)."
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when the notification was logged."
    )

    class Meta:
        indexes = [
            Index(fields=['tenant', 'created_at']),
        ]

    def __str__(self):
        """String showing recipient and channel."""
        return f"Notification to {self.tenant.full_name} via {self.channel}"
