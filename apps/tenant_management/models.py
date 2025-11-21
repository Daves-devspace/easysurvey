# apps/tenant_management/models.py
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN
from django.db import models, transaction
from django.utils import timezone
from django.db.models import F, Index, UniqueConstraint, Q, Sum
from django.core.exceptions import ValidationError

import logging

logger = logging.getLogger(__name__)


# ---------- Core models ----------
class WaterCompany(models.Model):
    name = models.CharField(max_length=255, unique=True)
    contact_info = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name


class Property(models.Model):
    SHARED = 'shared'
    METER = 'meter'
    PREPAID = 'prepaid'
    WATER_POLICY_CHOICES = [(SHARED, 'Shared'), (METER, 'Per Meter'), (PREPAID, 'Prepaid')]

    name = models.CharField(max_length=100, db_index=True)
    location = models.CharField(max_length=255)
    water_policy = models.CharField(max_length=50, choices=WATER_POLICY_CHOICES, default=SHARED, db_index=True)
    water_company = models.ForeignKey(WaterCompany, on_delete=models.PROTECT, related_name="properties")
    billing_day = models.PositiveSmallIntegerField(
        default=1,
        help_text="Day of the month when rent invoices are generated (e.g., 5 = 5th of each month)."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [Index(fields=['water_policy'])]

    def __str__(self):
        return self.name


class WaterRate(models.Model):
    water_company = models.ForeignKey(WaterCompany, on_delete=models.CASCADE, related_name='water_rates')
    rate_per_cubic_meter = models.DecimalField(max_digits=10, decimal_places=2)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ['-effective_from']
        indexes = [Index(fields=['water_company', 'is_active'])]
        unique_together = ('water_company', 'effective_from')
        constraints = [
            UniqueConstraint(fields=['water_company'], condition=Q(is_active=True), name="unique_active_rate_per_company")
        ]

    def clean(self):
        if self.is_active:
            qs = WaterRate.objects.filter(water_company=self.water_company, is_active=True)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError("Only one active water rate allowed per company.")

    def __str__(self):
        return f"{self.water_company.name} - {self.rate_per_cubic_meter}/m³ (from {self.effective_from})"


class Unit(models.Model):
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name='units')
    unit_number = models.CharField(max_length=50)
    rent_amount = models.DecimalField(max_digits=10, decimal_places=2)
    is_occupied = models.BooleanField(default=False, db_index=True)
    meter_number = models.CharField(max_length=50, null=True, blank=True)

    class Meta:
        unique_together = ('property', 'unit_number')
        indexes = [Index(fields=['property', 'unit_number'])]

    def __str__(self):
        return f"{self.property.name} - {self.unit_number}"


class Tenant(models.Model):
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="tenants")
    full_name = models.CharField(max_length=100)
    phone_number = models.CharField(max_length=15)
    email = models.EmailField(blank=True, null=True)
    national_id = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            UniqueConstraint(fields=['property', 'phone_number'], name='unique_tenant_phone_per_property'),
            UniqueConstraint(fields=['property', 'national_id'], name='unique_tenant_id_per_property'),
        ]
        indexes = [Index(fields=['property', 'phone_number']), Index(fields=['property', 'national_id'])]

    def __str__(self):
        return f"{self.full_name} ({self.property.name})"


class Lease(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='leases')
    unit = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name="leases")
    start_date = models.DateField()
    end_date=models.DateField(null=True, blank=True)
    deposit_amount = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        indexes = [
            # Add index for is_active (commonly filtered)
            models.Index(fields=['is_active']),
            # Add composite index for tenant and is_active
            models.Index(fields=['tenant', 'is_active']),
        ]
        constraints = [UniqueConstraint(fields=['unit'], condition=Q(is_active=True), name='unique_active_lease_per_unit')]

    def __str__(self):
        return f"Lease: {self.tenant.full_name} -> {self.unit}"

    def end_lease(self):
        self.is_active = False
        self.save()

    # @classmethod
    # def get_active_leafrom apps.tenant_management.utils.date_helpers import ses(cls):
    #     return cls.objects.filter(is_active=True)

    def clean(self):
        """
        Ensure tenant and unit belong to the same property.
        Defensive: only runs if both foreign keys are set.
        Uses *_id fields to avoid triggering RelatedObjectDoesNotExist
        when related objects are not yet loaded.
        """
        # Skip check if tenant or unit not yet set
        if not self.tenant_id or not self.unit_id:
            return

        # Fetch property ids directly from DB (efficient + safe)
        tenant_prop_id = Tenant.objects.filter(pk=self.tenant_id).values_list("property_id", flat=True).first()
        unit_prop_id = Unit.objects.filter(pk=self.unit_id).values_list("property_id", flat=True).first()

        # Only validate when both lookups succeeded
        if tenant_prop_id and unit_prop_id and tenant_prop_id != unit_prop_id:
            raise ValidationError("Tenant and Unit must belong to the same property.")



class MeterReading(models.Model):
    unit = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name='meter_readings')
    reading_date = models.DateField(
        default=timezone.now,  # allows overriding when creating in tests or views
        db_index=True
    )
    previous_reading = models.DecimalField(max_digits=10, decimal_places=2)
    current_reading = models.DecimalField(max_digits=10, decimal_places=2,null=True, blank=True)
    usage = models.DecimalField(max_digits=10, decimal_places=2, editable=False, null=True, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, editable=False, null=True, blank=True)
    rate_per_cubic_meter = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, editable=False)

    class Meta:
        get_latest_by = 'reading_date'
        indexes = [
            # Add composite index for unit and reading_date
            models.Index(fields=['unit', 'reading_date']),
            # Add index for current_reading (for filtering NULL values)
            models.Index(fields=['current_reading']),
        ]

    def __str__(self):
        return f"{self.unit} @ {self.reading_date}: {self.usage} m³"


class Invoice(models.Model):
    STATUS_DRAFT = "DRAFT"
    STATUS_PENDING = "PENDING"       # rent present, waiting for water
    STATUS_FINALIZED = "FINALIZED"   # ready / sent
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_PENDING, "Pending Readings"),
        (STATUS_FINALIZED, "Finalized"),
    ]

    tenant = models.ForeignKey("Tenant", on_delete=models.CASCADE, related_name="invoices")
    billing_period_start = models.DateField()
    billing_period_end = models.DateField()
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, editable=False, default=Decimal('0.00'))

    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True)

    is_paid = models.BooleanField(default=False, db_index=True)
    auto_generated = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            # Add composite index for tenant and status
            models.Index(fields=['tenant', 'status']),
            # Add index for billing_period_start for range queries
            models.Index(fields=['billing_period_start']),
            # Add index for billing_period_end for range queries
            models.Index(fields=['billing_period_end']),
            # Add composite index for tenant and is_paid
            models.Index(fields=['tenant', 'is_paid']),
        ]
        unique_together = ('tenant', 'billing_period_start', 'billing_period_end')

    def __str__(self):
        return f"Invoice #{self.id} - {self.tenant.full_name} ({self.billing_period_start} to {self.billing_period_end})"

    @property
    def total_paid(self):
        """
        ROBUST: Only count Payment records linked to this invoice.
        No longer considers negative InvoiceLines.
        """
        return sum(p.amount for p in self.payments.all())
    
    @property
    def balance(self):
        """
        ROBUST: Simple calculation using only Payment records.
        total_amount = sum of positive InvoiceLines (charges only)
        total_paid = sum of Payment records
        balance = charges - payments
        """
        from apps.tenant_management.helpers.money_helpers import quantize_money as q
        return q(self.total_amount) - q(self.total_paid)

    def finalize(self):
        """Called after all required lines are present (rent + water)."""
        if self.status != self.STATUS_FINALIZED:
            self.status = self.STATUS_FINALIZED
            self.save(update_fields=["status"])

    def mark_paid(self):
        """Called when balance is fully settled via Payment records."""
        if not self.is_paid:
            self.is_paid = True
            self.save(update_fields=["is_paid"])
        
    def recalc_total(self, save=True):
        """
        ROBUST: Only sum positive amounts from InvoiceLines.
        Negative payment amounts are no longer stored as InvoiceLines.
        """
        total = self.lines.filter(amount__gt=0).aggregate(s=Sum('amount')).get('s') or Decimal('0.00')
        self.total_amount = total
        if save:
            self.save(update_fields=['total_amount'])
        return total

    def update_status_for_lease(self, lease):
        """
        Decide invoice.status with respect to a particular lease.
        - If both rent and water lines exist for this lease in this invoice -> FINALIZED
        - If rent exists but water missing -> PENDING
        - If neither exists -> DRAFT
        Note: invoice is per-tenant; this is a conservative approach that finalizes
        when the given lease is complete.
        """
        rent_exists = self.lines.filter(lease=lease, line_type=InvoiceLine.LINE_RENT).exists()
        water_exists = self.lines.filter(lease=lease, line_type=InvoiceLine.LINE_WATER).exists()

        if rent_exists and water_exists:
            new_status = Invoice.STATUS_FINALIZED
        elif rent_exists and not water_exists:
            new_status = Invoice.STATUS_PENDING
        else:
            new_status = Invoice.STATUS_DRAFT

        if self.status != new_status:
            self.status = new_status
            self.save(update_fields=['status'])
            
    @property
    def due_date(self):
        """
        Compute invoice due date based on the tenant's property.billing_day.
        Ensures the due date is always a valid day in the month.
        """
        
        billing_day = self.tenant.property.billing_day
        # Ensure due day is within the current month
        year = self.billing_period_end.year
        month = self.billing_period_end.month
        last_day_of_month = (date(year + int(month / 12), (month % 12) + 1, 1) - timedelta(days=1)).day
        day = min(billing_day, last_day_of_month)
        return date(year, month, day)
            
            
            


class InvoiceLine(models.Model):
    LINE_RENT = 'RENT'
    LINE_WATER = 'WATER'
    LINE_DEPOSIT = 'DEPOSIT'
    LINE_REFUND = 'REFUND'
    LINE_OTHER = 'OTHER'
    LINE_TYPES = [
        (LINE_RENT, 'Rent'),
        (LINE_WATER, 'Water'),
        (LINE_DEPOSIT, 'DepositApplied'),
        (LINE_REFUND, 'Refund'),
        (LINE_OTHER, 'Other')
    ]

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    meter_reading = models.ForeignKey('MeterReading', null=True, blank=True, on_delete=models.SET_NULL, related_name='invoice_lines')
    lease = models.ForeignKey(Lease, null=True, blank=True, on_delete=models.SET_NULL, related_name="invoice_lines")
    deposit = models.ForeignKey('Deposit', null=True, blank=True, on_delete=models.SET_NULL, related_name='applied_lines')
    line_type = models.CharField(max_length=20, choices=LINE_TYPES, default=LINE_OTHER, db_index=True)
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self):
        lease_info = f" ({self.lease.unit.unit_number})" if self.lease else ""
        return f"{self.description}{lease_info}: {self.amount}"


class Payment(models.Model):
    """
    ROBUST: Payment records are the single source of truth for all payments.
    Each payment is linked to a specific tenant and optionally to a specific invoice.
    """
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='payments')
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='payments', null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_date = models.DateTimeField(auto_now_add=True, db_index=True)
    method = models.CharField(max_length=50, default='Mpesa')
    reference = models.CharField(max_length=100, null=True, blank=True)
    
    # Additional fields for better payment tracking
    payment_type = models.CharField(max_length=20, choices=[
        ('RENT', 'Rent Payment'),
        ('DEPOSIT', 'Deposit Payment'),
        ('MIXED', 'Mixed Payment'),
        ('CREDIT', 'Credit Application'),
    ], default='MIXED')
    
    #balance_after as it's now calculated from TenantBalance
    
    class Meta:
        indexes = [
            # Add composite index for tenant and payment_date
            models.Index(fields=['tenant', 'payment_date']),
            # Add index for payment_type
            models.Index(fields=['payment_type']),
            # Add composite index for invoice and payment_date
            models.Index(fields=['invoice', 'payment_date']),
        ]

    def __str__(self):
        if self.invoice is None:
            return f"Payment of {self.amount} by {self.tenant.full_name} (unallocated)"
        return f"Payment of {self.amount} for Invoice {self.invoice.id}"

    def save(self, *args, **kwargs):
        """
        ROBUST: Auto-update invoice payment status when Payment is saved.
        """
        super().save(*args, **kwargs)
        
        # Update invoice payment status if this payment is linked to an invoice
        if self.invoice:
            if self.invoice.balance <= 0 and not self.invoice.is_paid:
                self.invoice.mark_paid()


class TenantBalance(models.Model):
    tenant = models.OneToOneField(Tenant, on_delete=models.CASCADE, related_name="balance")
    balance = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        indexes = [Index(fields=['tenant']),
                   models.Index(fields=['balance']),]
        

    def __str__(self):
        return f"TenantBalance {self.tenant.full_name}: {self.balance}"

    @staticmethod
    def recalc_for_tenant(tenant, use_logger: bool = True):
        """
        ROBUST: Recalculate tenant balance using only Payment records and Invoice balances.
        No longer considers LedgerEntry credits/debits for balance calculation.
        
        New Logic:
        - Sum all unpaid invoice balances
        - Subtract unallocated payment credits (payments not linked to specific invoices)
        - Add any additional debits from LedgerEntry (if needed for special cases)
        """
        def _quantize(value):
            return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

        def log(msg):
            if use_logger:
                logger.debug(msg)
            else:
                print(msg)

        # Sum all unpaid invoice balances (using the robust Invoice.balance property)
        invoices = Invoice.objects.filter(tenant=tenant, is_paid=False)
        total_invoice_balance = sum(_quantize(inv.balance) for inv in invoices)
        log(f"[DEBUG] Total unpaid invoice balance for tenant {tenant}: {total_invoice_balance}")

        # Find unallocated payments (payments not linked to any invoice)
        unallocated_payments = Payment.objects.filter(tenant=tenant, invoice__isnull=True)
        total_unallocated_credit = sum(_quantize(p.amount) for p in unallocated_payments)
        log(f"[DEBUG] Total unallocated payment credits: {total_unallocated_credit}")

        # Include any additional debits from LedgerEntry (for special cases like fees)
        additional_debits = LedgerEntry.objects.filter(
            tenant=tenant, 
            debit__gt=0,
            invoice__isnull=True  # Only count debits not already included in invoices
        )
        total_additional_debit = sum(_quantize(le.debit) for le in additional_debits)
        log(f"[DEBUG] Additional debits from ledger: {total_additional_debit}")

        # Calculate final tenant balance
        balance = _quantize(total_invoice_balance + total_additional_debit - total_unallocated_credit)
        log(f"[DEBUG] Calculated tenant balance={balance}")

        # Update or create TenantBalance
        tenant_balance_obj, _ = TenantBalance.objects.get_or_create(tenant=tenant)
        tenant_balance_obj.balance = balance
        tenant_balance_obj.save(update_fields=["balance"])
        log(f"[DEBUG] TenantBalance updated for tenant {tenant}: {tenant_balance_obj.balance}")

        return tenant_balance_obj








class LedgerEntry(models.Model):
    DEPOSIT = 'deposit'
    RENT = 'rent'
    ENTRY_TYPE_CHOICES = [(DEPOSIT, 'Deposit'), (RENT, 'Rent')]

    lease = models.ForeignKey(Lease, on_delete=models.CASCADE, null=True, blank=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, null=True, blank=True)
    invoice = models.ForeignKey(Invoice, on_delete=models.SET_NULL, null=True, blank=True)
    deposit = models.ForeignKey('Deposit', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    debit = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    credit = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    entry_type = models.CharField(max_length=20, choices=ENTRY_TYPE_CHOICES, default=DEPOSIT)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"LedgerEntry {self.pk} ({self.entry_type}) D={self.debit} C={self.credit}"


class Deposit(models.Model):
    lease = models.ForeignKey(Lease, on_delete=models.CASCADE, related_name='deposits')
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='deposits')
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    amount_held = models.DecimalField(max_digits=14, decimal_places=2,default=Decimal('0.00'))
    paid_at = models.DateTimeField(null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)
    refunded_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-paid_at']

    def __str__(self):
        return f"Deposit #{self.pk} {self.amount} (held {self.amount_held}) for lease {self.lease_id}"

    def refund(self, amount=None):
        """
        Refund deposit: reduces amount_held, sets refunded_amount, creates ledger entry.
        Only admin should trigger this at lease end.
        """
        from apps.tenant_management.billing.services import refund_deposit as svc_refund
        return svc_refund(self, amount=amount)

    def apply_to_invoice(self, invoice, amount=None):
        """
        Only admin can apply deposit to an invoice manually.
        By default, this does nothing automatically on invoice creation.
        """
        from apps.tenant_management.billing.services import apply_deposit_to_invoice as svc_apply
        return svc_apply(self, invoice, amount=amount)



class Receipt(models.Model):
    payment = models.OneToOneField(Payment, on_delete=models.CASCADE)
    receipt_number = models.CharField(max_length=50, unique=True)
    issued_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [Index(fields=['receipt_number'])]

    def __str__(self):
        return f"Receipt #{self.receipt_number}"


class NotificationLog(models.Model):
    SMS = 'SMS'
    EMAIL = 'EMAIL'
    WHATSAPP = 'WHATSAPP'
    CHANNEL_CHOICES = [(SMS, 'SMS'), (EMAIL, 'Email'), (WHATSAPP, 'WhatsApp')]

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    message = models.TextField()
    channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES)
    status = models.CharField(max_length=20, default='sent')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [Index(fields=['tenant', 'created_at'])]

    def __str__(self):
        return f"Notification to {self.tenant.full_name} via {self.channel}"
