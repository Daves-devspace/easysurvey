# apps/tenant_management/views.py
from decimal import Decimal, InvalidOperation
from django.views.generic import FormView
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.contrib import messages
from django.db import transaction

from apps.tenant_management.forms import PaymentForm
from apps.tenant_management.models import Tenant, Invoice
# apply_payment_safe is defined in your signals module (you implemented it there)
from apps.tenant_management.signals import apply_payment_safe


from django.views import View
from django.contrib.admin.views.decorators import staff_member_required
from django.utils.decorators import method_decorator
from django.shortcuts import render
from django.utils import timezone
from datetime import date

from apps.tenant_management.tasks import generate_monthly_invoices
from apps.tenant_management.billings.utils import generate_monthly_invoices_for_all_leases


from django.http import JsonResponse


class TenantPaymentModalView(FormView):
    """
    Handles tenant payments (modal POST target).
    Accepts optional invoice_id — when provided, the payment attempts to target that invoice,
    otherwise it is treated as a general tenant payment (credit) and your apply_payment logic
    will allocate it automatically.
    """
    form_class = PaymentForm
    template_name = "tenant_management/payment_modal_stub.html"  # not used for modal POST; kept for completeness
    success_url = reverse_lazy("tenant_list")  # fallback

    def dispatch(self, request, *args, **kwargs):
        # ensure tenant exists
        self.tenant = get_object_or_404(Tenant, pk=kwargs.get("tenant_id"))
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        # prefer to return to referring page when possible
        return self.request.META.get("HTTP_REFERER") or super().get_success_url()

    def form_valid(self, form):
        amount = form.cleaned_data["amount"]
        invoice_id = form.cleaned_data.get("invoice_id")
        reference = form.cleaned_data.get("reference")
        method = form.cleaned_data.get("method") or "Mpesa"

        # Resolve invoice if provided (ensure it's the same tenant and unpaid)
        invoice = None
        if invoice_id:
            invoice = Invoice.objects.filter(pk=invoice_id, tenant=self.tenant).first()
            if invoice and invoice.is_paid:
                invoice = None  # ignore paid invoice; let allocation logic decide

        try:
            with transaction.atomic():
                # apply_payment_safe handles allocation, deposit top-ups, tenant credits, ledger entries etc.
                apply_payment_safe(
                    tenant=self.tenant,
                    payment_amount=Decimal(amount),
                    reference=reference,
                    method=method,
                    apply_to_deposit=True,
                    invoice=invoice
                )
        except Exception as e:
            # log exception in real code; return an error message to user
            messages.error(self.request, f"Failed to apply payment: {str(e)}")
            return redirect(self.get_success_url())

        messages.success(self.request, f"Payment of {amount} submitted successfully.")
        return redirect(self.get_success_url())

    def form_invalid(self, form):
        # create helpful message and return to referring page
        messages.error(self.request, "Invalid payment data. Please correct the highlighted fields.")
        return redirect(self.get_success_url())




@method_decorator(staff_member_required, name="dispatch")
class ManualInvoiceGenerationView(View):
    """
    Backend endpoint for manual invoice generation.
    POST-only: returns JSON result (no full page render).
    """

    def post(self, request, *args, **kwargs):
        run_date_str = request.POST.get("run_date")
        ref_date = date.fromisoformat(run_date_str) if run_date_str else timezone.now().date()

        result = generate_monthly_invoices_for_all_leases(ref_date)

        return JsonResponse({
            "success": True,
            "ref_date": str(ref_date),
            "created": result.get("created", 0),
            "updated": result.get("updated", 0),
            "skipped": result.get("skipped", 0),
        })