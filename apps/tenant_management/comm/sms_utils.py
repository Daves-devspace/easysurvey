import logging
from apps.tenant_management.models import Tenant, InvoiceLine, Property, Invoice, NotificationLog
from .mobile_sasa import MobileSasaAPI
from django.utils import timezone

logger = logging.getLogger(__name__)

def generate_invoice_message(invoice, override_balance=None):
    """
    Generates the detailed invoice message string.
    
    Args:
        invoice: The invoice object to generate details from.
        override_balance (Decimal): If provided, this is used as the NET PAYABLE amount.
                                    Useful for reminders where we want to show the 
                                    Cumulative Lease Balance (Arrears - Credits).
    """
    tenant = invoice.tenant
    lease = invoice.lease
    
    # 1. Identify Context (Property & Unit)
    if lease:
        property_name = lease.unit.property.name
        unit_ref = f"Unit {lease.unit.unit_number}"
        water_policy = lease.unit.property.water_policy
    else:
        property_name = tenant.property.name
        unit_ref = "your unit"
        water_policy = tenant.property.water_policy

    # 2. Billing Period
    period_start = invoice.billing_period_start.strftime('%d %b')
    period_end = invoice.billing_period_end.strftime('%d %b %Y')
    billing_period = f"{period_start} - {period_end}"
    
    lines = [f"Dear {tenant.full_name}, Invoice #{invoice.id} for {property_name} ({unit_ref}) is ready."]
    lines.append(f"Period: {billing_period}")
    
    # 3. Add Line Items
    rent_line = invoice.lines.filter(line_type=InvoiceLine.LINE_RENT).first()
    if rent_line:
        lines.append(f"Rent: KES {rent_line.amount:,.0f}")
        
    if water_policy == Property.METER:
        water_line = invoice.lines.filter(line_type=InvoiceLine.LINE_WATER).first()
        if water_line and water_line.meter_reading:
            mr = water_line.meter_reading
            prev_date = mr.previous_reading_date or invoice.billing_period_start
            curr_date = mr.reading_date
            
            date_range = f"{prev_date.strftime('%d/%m')} to {curr_date.strftime('%d/%m')}"
            details = f"Water ({date_range}): {mr.usage} units @ {mr.rate_per_cubic_meter}"
            lines.append(f"{details} = KES {water_line.amount:,.0f}")
        elif water_line:
             lines.append(f"Water: KES {water_line.amount:,.0f}")

    deposit_line = invoice.lines.filter(line_type=InvoiceLine.LINE_DEPOSIT).first()
    if deposit_line:
        lines.append(f"Deposit: KES {deposit_line.amount:,.0f}")

    # 4. Financial Totals
    lines.append(f"Total Bill: KES {invoice.total_amount:,.0f}")
    
    # Determine the Final Balance to display
    # If override provided (from Reminder View), use it. Else use specific invoice balance.
    final_balance = override_balance if override_balance is not None else invoice.balance
    
    # Calculate Context (Arrears vs Credit)
    # Difference between what is Owed Now vs What this Month Cost
    diff = final_balance - invoice.total_amount
    
    if diff > 0:
        lines.append(f"Arrears: KES {diff:,.0f}")
    elif diff < 0:
        lines.append(f"Credit/Paid: KES {abs(diff):,.0f}")
    
    lines.append(f"NET PAYABLE: KES {final_balance:,.0f}")
    lines.append(f"Due: {invoice.due_date.strftime('%d-%m-%Y')}")
    lines.append("Pay via M-Pesa.")

    return "\n".join(lines)

def send_invoice_notification(invoice):
    """
    Sends the generated message via MobileSasa.
    """
    tenant = invoice.tenant
    message = generate_invoice_message(invoice)

    try:
        api = MobileSasaAPI()
        
        if not tenant.phone_number:
            logger.warning(f"Skipping SMS for Invoice #{invoice.id}: Tenant has no phone number.")
            return False

        success = api.send_single_sms(tenant, message)
        return success
        
    except Exception as e:
        logger.error(f"Failed to send SMS for Invoice #{invoice.id}: {e}")
        return False

    
def send_sms_with_log(tenant, message):
    """
    Helper to send SMS and create/update NotificationLog with error details.
    """
    log_entry = NotificationLog.objects.create(
        tenant=tenant,
        message=message,
        channel=NotificationLog.SMS,
        status='queued'
    )
    
    try:
        if not tenant.phone_number:
            raise ValueError("Tenant has no phone number")

        api = MobileSasaAPI()
        # Assuming api.send_single_sms returns True/False or dict. 
        # Ideally, we update MobileSasaAPI to return response dict for better error handling.
        # For now, let's assume it returns True on success.
        # If MobileSasaAPI logs internally, we might be duplicating, but this ensures we control the 'error_details'.
        # Refactoring MobileSasaAPI.send_single_sms to return (success, error_msg) would be better, 
        # but let's wrap the existing call.
        
        # We'll use the API directly here to get response if possible, 
        # OR just rely on exception handling if send_single_sms raises them.
        # Let's assume MobileSasaAPI.send_single_sms returns a boolean for simplicity based on previous context.
        
        success = api.send_single_sms(tenant, message)
        
        if success:
            log_entry.status = 'sent'
            log_entry.save()
            return True
        else:
            log_entry.status = 'failed'
            log_entry.error_details = "Provider returned failure status"
            log_entry.save()
            return False

    except Exception as e:
        log_entry.status = 'failed'
        log_entry.error_details = str(e)
        log_entry.save()
        logger.error(f"SMS Failed: {e}")
        return False

def retry_notification(log_id):
    """
    Retries sending a failed notification log.
    """
    try:
        log = NotificationLog.objects.get(pk=log_id)
        
        # Only retry SMS for now
        if log.channel != NotificationLog.SMS:
            return False, "Unsupported channel"
            
        api = MobileSasaAPI()
        success = api.send_single_sms(log.tenant, log.message)
        
        if success:
            log.status = 'sent'
            log.error_details = None # Clear error on success
            log.created_at = timezone.now() # Update time? Or keep original? Let's keep original for record, maybe add 'updated_at'
            log.save()
            return True, "Resent successfully"
        else:
            return False, "Provider returned failure again"
            
    except NotificationLog.DoesNotExist:
        return False, "Log not found"
    except Exception as e:
        return False, str(e)


def get_bulk_invoice_targets(target_month, mode):
    """
    Returns a list of Invoices based on the mode.
    
    Mode 'new':
    - Invoice is Finalized.
    - Matches target month.
    - NO notification log exists for this tenant in this month containing 'invoice'.
    
    Mode 'reminder':
    - Invoice is Finalized.
    - Matches target month.
    - Balance > 0.
    """
    # 1. Base Query: Finalized invoices for the month
    invoices = Invoice.objects.filter(
        billing_period_start__year=target_month.year,
        billing_period_start__month=target_month.month,
        status=Invoice.STATUS_FINALIZED
    ).select_related('tenant', 'tenant__property')

    targets = []
    
    # 2. Fetch relevant logs to exclude sent ones
    # We check logs created AFTER the billing start date
    logs_qs = NotificationLog.objects.filter(
        created_at__gte=target_month,
        message__icontains="invoice" # Simple keyword check
    ).values_list('tenant_id', flat=True)
    
    sent_tenant_ids = set(logs_qs)

    for inv in invoices:
        if not inv.tenant.phone_number:
            continue
            
        if mode == 'new':
            # Only add if NOT in sent_tenant_ids
            if inv.tenant_id not in sent_tenant_ids:
                targets.append(inv)
                
        elif mode == 'reminder':
            # Only add if money is owed
            if inv.balance > 0:
                targets.append(inv)
                
    return targets

def generate_preview_message(invoice, mode):
    """Generates the text for preview."""
    tenant = invoice.tenant
    
    if mode == 'new':
        return (
            f"Dear {tenant.full_name}, invoice #{invoice.id} for {tenant.property.name} is ready. "
            f"Total: KES {invoice.total_amount:,.0f}. Due: {invoice.due_date}. "
            f"Pay via M-Pesa."
        )
    else:
        return (
            f"REMINDER: Dear {tenant.full_name}, you have an outstanding balance of KES {invoice.balance:,.0f} "
            f"for {tenant.property.name}. Please clear immediately to avoid penalties."
        )


def send_bulk_invoice_notifications(invoices):
    """
    Sends personalized invoice SMS to a list of invoices efficiently.
    (Currently unimplemented for bulk-personalized to keep simple, 
     but loop wrapper could be added here).
    """
    count = 0
    for inv in invoices:
        if send_invoice_notification(inv):
            count += 1
    return count

def send_property_announcement(property_obj, message_text):
    """
    Sends a generic announcement to ALL active tenants in a property.
    """
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