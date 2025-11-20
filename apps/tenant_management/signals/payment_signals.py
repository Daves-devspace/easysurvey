from django.db.models.signals import post_save
from django.dispatch import receiver
from apps.tenant_management.models import Payment, Receipt
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)

@receiver(post_save, sender=Payment)
def handle_payment_created(sender, instance, created, **kwargs):
    """
    Handle payment creation - create receipt and update invoice status.
    """
    if not created:
        return

    try:
        # Create receipt for all payments
        Receipt.objects.create(
            payment=instance,
            receipt_number=f"RCP-{timezone.now().strftime('%Y%m%d%H%M%S')}-{instance.pk}"
        )

        if not instance.invoice:
            # This is a credit payment (invoice=None)
            logger.info(f"Created receipt for credit payment {instance.pk}")
        else:
            # This is an invoice payment
            logger.info(f"Created receipt for invoice payment {instance.pk}")
            
            # Mark invoice as paid if balance is zero
            instance.invoice.refresh_from_db()
            if instance.invoice.balance <= 0 and not instance.invoice.is_paid:
                instance.invoice.mark_paid()
                logger.info(f"Invoice {instance.invoice.pk} marked as paid after payment {instance.pk}")
            
    except Exception as e:
        logger.error(f"Error processing payment {instance.pk}: {e}")
        # Still create a receipt even if there's an error
        try:
            Receipt.objects.create(
                payment=instance,
                receipt_number=f"RCP-{timezone.now().strftime('%Y%m%d%H%M%S')}-{instance.pk}"
            )
        except:
            logger.error(f"Failed to create receipt for payment {instance.pk}")