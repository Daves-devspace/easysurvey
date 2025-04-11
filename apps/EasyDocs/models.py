from decimal import Decimal

from django.conf import settings
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
    file = models.FileField(upload_to='student_documents/', blank=True, null=True, help_text="Upload student-related documents")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.doc_name} - {self.reference} ({self.location})"

# Client Model
class Client(models.Model):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(unique=True, blank=True, null=True)
    phone = models.CharField(max_length=15)

    def __str__(self):
        return self.first_name

# Service Model
class Service(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    def __str__(self):
        return f"{self.name} – KSH {self.total_price}"

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
        return f"{self.service.name} – {self.name} (KES {self.cost})"


# Client Service Model
class ClientService(models.Model):
    client = models.ForeignKey(Client, related_name='client_services', on_delete=models.CASCADE)
    service = models.ForeignKey(Service, related_name='client_services', on_delete=models.CASCADE)
    land_description = models.CharField(max_length=255)
    requested_at = models.DateTimeField(auto_now_add=True)

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
    def sub_services_total(self):
        """
        Sum of all sub-services prices (unchanged).
        """
        total = self.sub_services.aggregate(
            total=Sum('sub_service__price')
        )['total']
        return total or Decimal('0.00')

    @property
    def full_total_price(self):
        """
        Grand total: processes + sub-services.
        """
        return self.processes_total + self.sub_services_total

    @property
    def total_price(self):
        total = Decimal('0.00')
        for csp in self.service_processes.all():
            if csp.overridden_cost is not None:
                total += csp.overridden_cost
            else:
                total += csp.process.cost  # or .cost if that's the field
        return total

    def total_balance(self):
        """
        Amount still owed: full total minus what’s been paid.
        """
        return self.full_total_price - self.total_paid()

    def __str__(self):
        return f"{self.client.first_name} - {self.service.name} for {self.land_description}"
#
# class ClientServiceProcess(models.Model):
#     STATUS_CHOICES = [
#         ('pending', 'Pending'),
#         ('in_progress', 'In Progress'),
#         ('completed', 'Completed'),
#         ('collected', 'Title Deed Collected'),
#     ]
#
#     client_service = models.ForeignKey(
#         ClientService,
#         related_name='service_processes',
#         on_delete=models.CASCADE
#     )
#     process = models.ForeignKey(
#         Process,
#         related_name='service_processes',
#         on_delete=models.CASCADE
#     )
#     status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
#     completed_at = models.DateTimeField(null=True, blank=True)
#
#     paid_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
#
#     @property
#     def total_paid(self):
#         return self.paid_amount
#
#     @property
#     def pending_amount(self):
#         return self.process.cost - self.paid_amount
#
#
#
#     def __str__(self):
#         return (
#             f"{self.client_service.client.first_name} - "
#             f"{self.process.name} "
#             f"(Paid: {self.paid_amount} | Pending: {self.pending_amount})"
#         )
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

    def __str__(self):
        return f"{self.client_service} → {self.sub_service.name}"

    @property
    def price(self):
        return self.sub_service.price

    @property
    def balance(self):
        return max(Decimal('0.00'), self.sub_service.price - self.paid_amount)




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



class Credit(models.Model):
    client = models.ForeignKey(Client, related_name='credits', on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    added_on = models.DateTimeField(auto_now_add=True)
    expiry_date = models.DateTimeField(null=True, blank=True)  # Optional expiry date

    def __str__(self):
        return f"{self.client.first_name}'s credit: KSH {self.amount}"

    def apply_credit(self, amount: Decimal):
        """
        Apply credit to a payment. If the credit is less than the amount, apply all of it.
        """
        if self.amount >= amount:
            self.amount -= amount
            self.save(update_fields=['amount'])
            return amount  # Full amount can be paid using credit
        else:
            applied_credit = self.amount
            self.amount = Decimal('0.00')
            self.save(update_fields=['amount'])
            return applied_credit  # Partial credit applied


class PaymentHistory(models.Model):
    client_service = models.ForeignKey('ClientService', related_name='payment_history', on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=20)
    transaction_id = models.CharField(max_length=100)
    timestamp = models.DateTimeField(auto_now_add=True)
    reason = models.CharField(max_length=255, blank=True, null=True)  # Reason for payment (credit, refund, etc.)

    def __str__(self):
        return f"PaymentHistory: {self.amount} by {self.client_service.client.first_name} for {self.payment_method}"

class Refund(models.Model):
    client_service = models.ForeignKey('ClientService', related_name='refunds', on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.CharField(max_length=255)  # Reason for the refund (excess payment, credit withdrawal)
    status = models.CharField(max_length=20, choices=[('pending', 'Pending'), ('completed', 'Completed')], default='pending')
    refund_date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Refund of {self.amount} for {self.client_service.client.first_name} due to {self.reason}"



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

    def save(self, *args, **kwargs):
        """
        On first save of a new Payment:
        - Persist the Payment record.
        - Allocate its amount across the ClientServiceProcess steps in order.
        - Store per-process allocations in each CSP.paid_amount field.
        - Apply available credit to reduce the total amount.
        """
        is_new = self.pk is None
        super().save(*args, **kwargs)

        if not is_new:
            return

        remaining: Decimal = Decimal(str(self.amount))

        # First, apply available credit from the client
        credit = self.client_service.client.credits.filter(amount__gt=0).first()
        if credit:
            applied_credit = credit.apply_credit(remaining)
            remaining -= applied_credit
            # Record the credit application in the payment history
            PaymentHistory.objects.create(
                client_service=self.client_service,
                amount=applied_credit,
                payment_method="credit",
                transaction_id="credit-applied",
                reason="Credit applied"
            )

        # Proceed with normal payment process (allocate funds to CSP)
        for csp in (
                self.client_service
                        .service_processes
                        .select_related('process')
                        .order_by('process__step_order')
        ):
            if remaining <= Decimal('0.00'):
                break
            balance: Decimal = Decimal(str(csp.pending_amount))
            to_pay: Decimal = min(remaining, balance)

            if to_pay > Decimal('0.00'):
                if csp.paid_amount is None:
                    csp.paid_amount = Decimal('0.00')
                csp.paid_amount += to_pay
                csp.save(update_fields=['paid_amount'])
                remaining -= to_pay

        # Allocate remaining to sub-services
        if remaining > Decimal('0.00'):
            for cs_sub in self.client_service.sub_services.all():
                balance = cs_sub.sub_service.price - cs_sub.paid_amount
                to_pay = min(remaining, balance)

                if to_pay > Decimal('0.00'):
                    cs_sub.paid_amount += to_pay
                    cs_sub.save(update_fields=['paid_amount'])
                    remaining -= to_pay
                    if remaining <= Decimal('0.00'):
                        break

        # Handle remaining funds (refund, overpayment, etc.)
        if remaining > Decimal('0.00'):
            # Create a Refund record for overpayment
            refund = Refund.objects.create(
                client_service=self.client_service,
                amount=remaining,
                reason="Excess payment after service and sub-service allocation"
            )
            # Record the refund in the PaymentHistory
            PaymentHistory.objects.create(
                client_service=self.client_service,
                amount=remaining,
                payment_method="refund",
                transaction_id="refund-issued",
                reason="Overpayment refunded"
            )


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

    def __str__(self):
        return f"Token: {self.sender_id}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        cache.delete('sms_provider_token')