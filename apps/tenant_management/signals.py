# signals.py
from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver
from .models import Lease, Unit

@receiver(post_save, sender=Lease)
def mark_unit_occupied(sender, instance, created, **kwargs):
    """
    When a new Lease is created or activated, mark its Unit occupied.
    """
    # Only on new leases or when re-activating
    if created and instance.is_active:
        Unit.objects.filter(pk=instance.unit_id).update(is_occupied=True)


@receiver(pre_save, sender=Lease)
def mark_unit_vacant_on_end(sender, instance, **kwargs):
    """
    When a Lease is updated to inactive, mark its Unit vacant before saving.
    """
    if not instance.pk:
        # New lease, no old state to compare
        return

    # Fetch old version to compare is_active flag
    old = Lease.objects.get(pk=instance.pk)
    if old.is_active and not instance.is_active:
        Unit.objects.filter(pk=instance.unit_id).update(is_occupied=False)
