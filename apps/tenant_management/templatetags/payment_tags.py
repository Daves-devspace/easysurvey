from django import template
from apps.tenant_management.models import Payment

register = template.Library()

@register.filter
def payment_allocations(payment):
    """
    Returns the child allocation payments for a given master payment.
    Usage: {% for child in payment|payment_allocations %}
    """
    if not payment or not payment.pk:
        return []
        
    return Payment.objects.filter(
        reference__startswith=f"Allocation from payment {payment.pk}"
    ).select_related('invoice')