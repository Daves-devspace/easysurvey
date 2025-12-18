import logging
from apps.tenant_management.models import Tenant
from .mobile_sasa import MobileSasaAPI

logger = logging.getLogger(__name__)

def send_invoice_notification(invoice):
    """
    Sends a single invoice notification to the tenant.
    Format: "Hello [Name], your invoice for [Period] is KES [Total]. Balance: [Balance]. Pay via..."
    """
    tenant = invoice.tenant
    
    # Construct Message
    message = (
        f"Hello {tenant.full_name}, your invoice for {invoice.billing_period_start.strftime('%b %Y')} "
        f"is KES {invoice.total_amount:,.2f}. "
        f"Current Due: KES {invoice.balance:,.2f}. "
        f"Due date: {invoice.due_date}. "
        f"Please pay to Till: 123456."
    )
    
    try:
        api = MobileSasaAPI()
        return api.send_single_sms(tenant, message)
    except Exception as e:
        logger.error(f"Failed to send invoice SMS to {tenant}: {e}")
        return False

def send_bulk_invoice_notifications(invoices):
    """
    Sends personalized invoice SMS to a list of invoices efficiently.
    """
    messages_data = []
    
    for invoice in invoices:
        tenant = invoice.tenant
        msg = (
            f"Hello {tenant.full_name}, invoice {invoice.id} for {invoice.billing_period_start.strftime('%b %Y')} "
            f"is generated. Total: {invoice.total_amount:,.0f}. Balance: {invoice.balance:,.0f}. "
            f"Due: {invoice.due_date}."
        )
        messages_data.append({'tenant': tenant, 'message': msg})
    
    if not messages_data:
        return 0

    try:
        api = MobileSasaAPI()
        return api.send_personalized_bulk(messages_data)
    except Exception as e:
        logger.error(f"Failed to send bulk invoice SMS: {e}")
        return 0

def send_property_announcement(property_obj, message_text):
    """
    Sends a generic announcement to ALL active tenants in a property.
    """
    # Get active tenants
    # Filter tenants who have at least one active lease in this property
    active_tenants = Tenant.objects.filter(
        property=property_obj,
        leases__is_active=True
    ).distinct()
    
    if not active_tenants.exists():
        return 0
        
    try:
        api = MobileSasaAPI()
        # Prepend Property Name for context
        full_message = f"[{property_obj.name}] {message_text}"
        return api.send_bulk_sms(full_message, active_tenants)
    except Exception as e:
        logger.error(f"Failed to send announcement: {e}")
        return 0