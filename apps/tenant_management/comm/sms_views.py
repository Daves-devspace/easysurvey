from django.shortcuts import get_object_or_404, render, redirect
from django.views import View
from django.contrib import messages 
from apps.tenant_management.models import Property, Tenant, NotificationLog, Lease, Invoice, Payment, InvoiceLine
from django.db.models import Q, Value, DecimalField
from django.db.models.functions import Coalesce
from django.db.models import F, Sum

from apps.tenant_management.forms import AnnouncementForm
from apps.tenant_management.comm import sms_utils
from django.http import HttpResponse
from apps.tenant_management.forms import BulkCommunicationForm
from apps.tenant_management.tasks import send_bulk_sms_task
from datetime import date
import logging
from decimal import Decimal, ROUND_HALF_UP
from apps.tenant_management.comm.mobile_sasa import MobileSasaAPI
logger = logging.getLogger(__name__)

# --- Helper for money quantization ---
CENTS = Decimal("0.01")
def q(amount):
    """
    Helper to quantize decimals to 2 places.
    Handles None and non-decimal inputs gracefully.
    """
    if amount is None: return Decimal("0.00")
    if not isinstance(amount, Decimal):
        try: amount = Decimal(str(amount))
        except: return Decimal("0.00")
    return amount.quantize(CENTS, rounding=ROUND_HALF_UP)


class AnnouncementPreviewView(View):
    def post(self, request, pk):
        property_obj = get_object_or_404(Property, pk=pk)
        form = AnnouncementForm(request.POST)
        
        if form.is_valid():
            message = form.cleaned_data['message']
            # Calculate Recipients: Active tenants in this property
            active_tenants = Tenant.objects.filter(property=property_obj, leases__is_active=True).distinct()
            recipient_count = active_tenants.count()
            
            # Simple cost estimation (approx 1 KES per SMS)
            estimated_cost = recipient_count * 1.0 
            
            context = {
                'property': property_obj,
                'message': message,
                'recipient_count': recipient_count,
                'estimated_cost': estimated_cost,
            }
            return render(request, 'properties/partials/comm_preview.html', context)
        
        return HttpResponse("Invalid Form", status=400)

class AnnouncementSendView(View):
    def post(self, request, pk):
        property_obj = get_object_or_404(Property, pk=pk)
        message = request.POST.get('message')
        
        if not message:
            messages.error(request, "Message cannot be empty.")
            return redirect('property_detail', pk=pk)

        # Send via Utils
        count = sms_utils.send_property_announcement(property_obj, message)
        
        if count > 0:
            messages.success(request, f"Announcement sent to {count} tenants.")
        else:
            messages.warning(request, "No active tenants found or failed to send.")
            
        return redirect('property_detail', pk=pk)
    
    
    
class BulkCommPreviewView(View):
    def post(self, request):
        form = BulkCommunicationForm(request.POST)
        if form.is_valid():
            mode = form.cleaned_data['mode']
            month = form.cleaned_data['target_month']
            
            target_invoices = sms_utils.get_bulk_invoice_targets(month, mode)
            recipient_count = len(target_invoices)
            
            samples = []
            for inv in target_invoices[:2]:
                msg = sms_utils.generate_preview_message(inv, mode)
                samples.append({'tenant': inv.tenant.full_name, 'msg': msg})
            
            # Fetch SMS Balance
            current_balance = 0.0
            try:
                
                api = MobileSasaAPI()
                resp = api.get_balance()
                # Adjust key based on actual API response structure if needed
                if resp.get('status'):
                    current_balance = float(resp.get('balance', 0))
            except Exception as e:
                logger.error(f"Error fetching SMS balance: {e}")
            
            estimated_cost = recipient_count * 1.0  # Approx 1 KES per SMS
            is_sufficient = current_balance >= estimated_cost

            context = {
                'mode': mode,
                'target_month': month.strftime("%Y-%m-%d"),
                'recipient_count': recipient_count,
                'estimated_cost': estimated_cost,
                'current_balance': current_balance,
                'is_sufficient': is_sufficient,
                'samples': samples
            }
            
            return render(request, 'properties/partials/bulk_comm_preview.html', context)
        
        # LOG ERRORS
        logger.error(f"Bulk Comm Form Errors: {form.errors}")
        return HttpResponse(f"Invalid Form: {form.errors.as_text()}", status=400)

class BulkCommSendView(View):
    def post(self, request):
        month_str = request.POST.get('target_month')
        mode = request.POST.get('mode')
        
        if not month_str or not mode:
            messages.error(request, "Missing parameters.")
            return redirect('property-list')
            
        month = date.fromisoformat(month_str)
        
        # Re-fetch targets to get IDs
        target_invoices = sms_utils.get_bulk_invoice_targets(month, mode)
        invoice_ids = [inv.id for inv in target_invoices]
        
        if not invoice_ids:
            messages.warning(request, "No eligible tenants found to message.")
            return redirect('property-list')
            
        # Trigger Task
        send_bulk_sms_task.delay(invoice_ids, mode)
        
        messages.success(request, f"Queued {len(invoice_ids)} messages for sending.")
        return redirect('property-list')
    
    
    
class CommLogRetryView(View):
    """
    Retries a single failed communication log.
    Intended for HTMX calls from the table row.
    """
    def post(self, request, pk):
        success, message = sms_utils.retry_notification(pk)
        
        if success:
            messages.success(request, f"Message resent.")
        else:
            messages.error(request, f"Retry failed: {message}")
            
        # Return to the list (Property List or Detail depending on referer)
        return redirect(request.META.get('HTTP_REFERER', 'property-list'))

class CommLogRetryAllView(View):
    """
    Retries ALL failed messages (optionally filtered by property).
    """
    def post(self, request):
        property_id = request.POST.get('property_id')
        
        qs = NotificationLog.objects.filter(status='failed')
        if property_id:
            qs = qs.filter(tenant__property_id=property_id)
            
        failed_logs = list(qs)
        count = 0
        
        for log in failed_logs:
            # We could offload this to a Celery task for bulk
            success, _ = sms_utils.retry_notification(log.id)
            if success: count += 1
            
        if count > 0:
            messages.success(request, f"Successfully resent {count} messages.")
        elif not failed_logs:
            messages.info(request, "No failed messages found to retry.")
        else:
            messages.warning(request, "Failed to resend messages. Check logs/balance.")
            
        return redirect(request.META.get('HTTP_REFERER', 'property-list'))
    
    
class LeaseReminderPreviewView(View):
    def get(self, request, lease_id):
        lease = get_object_or_404(Lease, pk=lease_id)
        tenant = lease.tenant
        
        # 1. Calculate TRUE Lease Balance (Mirroring 'utils.py' logic)
        invoice_lines = InvoiceLine.objects.filter(lease=lease)
        total_invoiced = invoice_lines.aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
        
        lease_invoices = Invoice.objects.filter(lease=lease)
        total_paid = Payment.objects.filter(
            invoice__in=lease_invoices
        ).exclude(payment_type='MIXED').aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
        
        tenant_credit = Payment.objects.filter(
            tenant=tenant, 
            invoice__isnull=True
        ).exclude(payment_type='MIXED').aggregate(
            t=Coalesce(Sum("amount"), Value(Decimal("0.00")), output_field=DecimalField())
        )["t"]
        
        net_balance = q(total_invoiced - total_paid - (tenant_credit or 0))
        
        if net_balance <= 0:
             return HttpResponse(
                 '<div class="modal-body"><div class="alert alert-success m-3">'
                 '<i class="bi bi-check-circle me-2"></i>This lease is fully paid (including credits).'
                 '</div></div>'
             )

        # 2. Construct Message
        latest_invoice = Invoice.objects.filter(lease=lease).order_by('-billing_period_start').first()
        
        if latest_invoice:
            message = sms_utils.generate_invoice_message(latest_invoice, override_balance=net_balance)
        else:
            property_name = lease.unit.property.name
            unit_ref = f"Unit {lease.unit.unit_number}"
            message = (
                f"Dear {tenant.full_name}, reminder for {property_name} ({unit_ref}). "
                f"Total Outstanding Balance: KES {net_balance:,.0f}. "
                f"Please pay via M-Pesa."
            )

        # Check SMS Balance
        current_balance = 0.0
        is_sufficient = False
        try:
            api = MobileSasaAPI()
            resp = api.get_balance()
            if resp.get('status'):
                current_balance = float(resp.get('balance', 0))
            is_sufficient = current_balance >= 1.0
        except Exception as e:
            logger.error(f"Error checking SMS balance: {e}")

        context = {
            'lease': lease,
            'message': message,
            'current_balance': current_balance,
            'is_sufficient': is_sufficient,
            'estimated_cost': 1.0
        }
        return render(request, 'properties/partials/reminder_preview.html', context)


class LeaseReminderSendView(View):
    def post(self, request, lease_id):
        lease = get_object_or_404(Lease, pk=lease_id)
        message = request.POST.get('message')
        
        if not message:
            messages.error(request, "Message cannot be empty.")
            return redirect(request.META.get('HTTP_REFERER', 'property-list'))

        try:
            api = MobileSasaAPI()
            success = api.send_single_sms(lease.tenant, message)
            if success:
                messages.success(request, f"Reminder sent to {lease.tenant.full_name}.")
            else:
                messages.error(request, "Failed to send SMS (Provider Error).")
        except Exception as e:
             messages.error(request, f"Error: {e}")
             
        return redirect(request.META.get('HTTP_REFERER', 'property_detail'))