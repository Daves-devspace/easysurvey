from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from apps.tenant_management.models import Invoice, InvoiceLine
from decimal import Decimal
from django.db.models import Sum

@receiver([post_save, post_delete], sender=InvoiceLine)
def update_invoice_total(sender, instance, **kwargs):
    """
    Recalculate invoice total whenever invoice lines change.
    Keeps Invoice.total_amount authoritative.
    """
    invoice = instance.invoice
    total = Decimal('0.00')
    agg = invoice.lines.aggregate(s=Sum('amount'))
    total = agg.get('s') or Decimal('0.00')
    invoice.total_amount = total
    invoice.save(update_fields=['total_amount'])