# apps/accounts/models.py
from django.db import models, transaction
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from decimal import Decimal
from django.db.models import UniqueConstraint, Q

User = get_user_model()


class CashbookEntry(models.Model):
    """
    Single canonical cashbook entry. All cash movements (IN/OUT) are represented here.
    We also allow a special 'opening snapshot' row flagged with is_opening_balance=True
    which carries forward the previous day's closing (amount=0, balance_after set).
    """
    ENTRY_TYPES = [
        ("IN", "Cash In"),
        ("OUT", "Cash Out"),
    ]

    entry_type = models.CharField(max_length=3, choices=ENTRY_TYPES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.CharField(max_length=255)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="cashbook_entries",
    )

    # Generic relation to link Payment, Expense, etc.
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True, blank=True)
    object_id = models.PositiveIntegerField(null=True, blank=True)
    related_object = GenericForeignKey("content_type", "object_id")

    # Running balance AFTER this entry (immutable once created)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    # date shorthand (used for unique opening-date constraint and simpler queries)
    entry_date = models.DateField(editable=False, db_index=True)

    # opening snapshot flag (only one per date allowed at DB level)
    is_opening_balance = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["entry_type"]),
        ]
        constraints = [
            # ensure at most one opening snapshot per entry_date
            UniqueConstraint(
                fields=["entry_date"],
                condition=Q(is_opening_balance=True),
                name="unique_opening_balance_date"
            )
        ]

    def __str__(self):
        return f"{self.get_entry_type_display()} – {self.amount} ({self.description})"

    # --------------------------
    # Utility / balance helpers
    # --------------------------
    @classmethod
    def current_balance(cls):
        """Return the latest balance or 0 if no entries exist."""
        last_entry = cls.objects.order_by("-created_at", "-id").first()
        return last_entry.balance_after if last_entry else Decimal("0.00")

    @classmethod
    def opening_balance(cls, date=None):
        """
        Computed opening balance as the closing balance at the end of previous day.
        This is used as fallback for legacy behavior when there is no persisted opening snapshot.
        """
        date = date or timezone.now().date()
        last_entry = cls.objects.filter(created_at__lt=date).order_by("-created_at", "-id").first()
        return last_entry.balance_after if last_entry else Decimal("0.00")

    @classmethod
    def closing_balance(cls, date=None):
        """
        Balance at the end of the given date.
        Uses balance_after of the last entry on/ before the date.
        """
        date = date or timezone.now().date()
        last_entry = cls.objects.filter(created_at__date__lte=date).order_by("-created_at", "-id").first()
        return last_entry.balance_after if last_entry else Decimal("0.00")

    # --------------------------
    # Recording helpers
    # --------------------------
    @classmethod
    def record_in(cls, amount: Decimal, description: str, related_object=None, created_by=None):
        """
        Create an IN entry and update running balance atomically.
        entry_date will be set automatically in save().
        """
        with transaction.atomic():
            prev_balance = cls.current_balance()
            new_balance = prev_balance + Decimal(amount)
            return cls.objects.create(
                entry_type="IN",
                amount=Decimal(amount),
                description=description,
                related_object=related_object,
                created_by=created_by,
                balance_after=new_balance,
            )

    @classmethod
    def record_out(cls, amount: Decimal, description: str, related_object=None, created_by=None, allow_negative=False):
        """
        Create an OUT entry and update running balance atomically.
        By default prevents negative balances unless allow_negative=True.
        """
        with transaction.atomic():
            prev_balance = cls.current_balance()
            amount = Decimal(amount)
            if not allow_negative and prev_balance < amount:
                raise ValueError("Insufficient balance for this cash out")
            new_balance = prev_balance - amount
            return cls.objects.create(
                entry_type="OUT",
                amount=amount,
                description=description,
                related_object=related_object,
                created_by=created_by,
                balance_after=new_balance,
            )

    # --------------------------
    # Opening helper (legacy name kept for compatibility)
    # --------------------------
    @classmethod
    def ensure_opening_balance(cls, user=None, date=None):
        """
        Backwards-compatible helper: create persisted opening snapshot if necessary.
        Delegates to higher-level helpers in services, but left here for compatibility.
        """
        from apps.accounts.services.cashbook import persist_opening_balance  # avoids import loop
        return persist_opening_balance(date=date or timezone.now().date(), user=user)
    
    
    def force_update_flagged_balance(self, new_balance: Decimal, user=None):
        """
        Update flagged opening balance directly, bypassing save() audit restriction.
        Only allowed for is_opening_balance=True.
        """
        if not self.is_opening_balance:
            raise ValueError("Cannot force update non-flagged entries")

        old_balance = self.balance_after
        # Direct DB update bypassing save()
        CashbookEntry.objects.filter(pk=self.pk).update(balance_after=new_balance)

        # Log audit
        desc = (
            f"Flagged opening balance force-updated for {self.entry_date}: "
            f"old_balance={old_balance}, new_balance={new_balance}"
        )
        from apps.accounts.services.opening_balance import log_audit
        log_audit(user=user, action="update", model_name="CashbookEntry", object_id=self.pk, description=desc)

    # --------------------------
    # Protect records (audit)
    # --------------------------
    def save(self, *args, **kwargs):
        if self.pk:
            orig = CashbookEntry.objects.get(pk=self.pk)
            # Prevent changing financial data
            if (
                self.amount != orig.amount
                or self.balance_after != orig.balance_after
                or self.entry_type != orig.entry_type
                or self.entry_date != orig.entry_date
                or self.is_opening_balance != orig.is_opening_balance
            ):
                raise ValueError("Financial fields cannot be modified (audit rule).")
        else:
            # First save → ensure entry_date
            created = self.created_at or timezone.now()
            self.entry_date = created.date()
        super().save(*args, **kwargs)


    def delete(self, *args, **kwargs):
        raise ValueError("Cashbook entries cannot be deleted (audit rule).")
