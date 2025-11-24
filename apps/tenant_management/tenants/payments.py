from decimal import Decimal
from django.views.generic import FormView
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse
from django.views import View
from django.contrib.admin.views.decorators import staff_member_required
from django.utils.decorators import method_decorator
from django.utils import timezone
from datetime import date

from apps.tenant_management.forms import PaymentForm
from apps.tenant_management.models import Tenant, Invoice

# Import the new Service Layer
from apps.tenant_management.services.payment_service import PaymentService
from apps.tenant_management.services.billing_cycle_service import BillingCycleService


class TenantPaymentModalView(FormView):
    """
    Handles tenant payments.
    - If invoice_id is provided: Pays that specific invoice.
    - If invoice_id is None: Acts as a 'Recharge' (FIFO allocation).
    """
    form_class = PaymentForm
    template_name = "tenant_management/payment_modal_stub.html"

    def dispatch(self, request, *args, **kwargs):
        self.tenant = get_object_or_404(Tenant, pk=kwargs.get("tenant_id"))
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        amount = form.cleaned_data["amount"]
        invoice_id = form.cleaned_data.get("invoice_id")
        reference = form.cleaned_data.get("reference")
        method = form.cleaned_data.get("method") or "Mpesa"

        # Resolve invoice if provided
        invoice = None
        if invoice_id:
            invoice = Invoice.objects.filter(pk=invoice_id, tenant=self.tenant).first()
            # If the invoice is already paid, ignore the ID and treat as general credit
            if invoice and invoice.is_paid:
                invoice = None 

        try:
            # Use Service Layer for robust transaction handling
            PaymentService.process_payment(
                tenant=self.tenant,
                amount=Decimal(amount),
                reference=reference,
                method=method,
                invoice=invoice
            )
            messages.success(self.request, f"Payment of {amount} processed successfully.")
        except Exception as e:
            messages.error(self.request, f"Payment failed: {str(e)}")
            
        return redirect(self.request.META.get("HTTP_REFERER", "/"))

    def form_invalid(self, form):
        messages.error(self.request, "Invalid payment details. Please check amount.")
        return redirect(self.request.META.get("HTTP_REFERER", "/"))


@method_decorator(staff_member_required, name="dispatch")
class ManualInvoiceGenerationView(View):
    """
    Backend endpoint for manual invoice generation (Rent Roll).
    Now uses BillingCycleService.
    """

    def post(self, request, *args, **kwargs):
        run_date_str = request.POST.get("run_date")
        ref_date = date.fromisoformat(run_date_str) if run_date_str else timezone.now().date()

        try:
            # Call the new Orchestrator
            result = BillingCycleService.generate_rent_roll(target_date=ref_date)

            return JsonResponse({
                "success": True,
                "ref_date": str(ref_date),
                "created": result.get("created", 0),
                "errors": result.get("errors", 0),
                # Note: 'updated'/'skipped' are no longer tracked in the new service 
                # structure, so we omit them or return 0.
            })
        except Exception as e:
            return JsonResponse({
                "success": False,
                "error": str(e)
            }, status=500)