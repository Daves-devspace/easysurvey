# services/legal_payout.py
from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect
from django.utils.dateparse import parse_date
from django.views import View

from apps.EasyDocs.models import LegalOfficePayout, ClientSubService

def create_legal_payout(subservices, paid_month):
    total = sum(s.balance for s in subservices)
    with transaction.atomic():
        payout = LegalOfficePayout.objects.create(
            total_amount=total,
            paid_month=paid_month
        )
        payout.subservices.add(*subservices)
        subservices.update(paid_to_legal_office=True, paid_month=paid_month)
    return payout


class BulkPayoutView(View):
    def post(self, request, *args, **kwargs):
        subservice_ids = request.POST.getlist('subservices')
        paid_month = request.POST.get('paid_month')

        if not subservice_ids:
            messages.error(request, "No sub-services selected.")
            return redirect('accounts_dashboard')

        if not paid_month:
            messages.error(request, "Please select a payout month.")
            return redirect('accounts_dashboard')

        try:
            payout_date = parse_date(paid_month + "-01")  # Normalize to first of month
        except Exception:
            messages.error(request, "Invalid payout month.")
            return redirect('accounts_dashboard')

        subservices = ClientSubService.objects.filter(id__in=subservice_ids)

        with transaction.atomic():
            for sub in subservices:
                # Example logic: only pay if balance remains
                if sub.overridden_price and sub.paid_amount >= sub.overridden_price:
                    continue  # already fully paid

                price = sub.overridden_price or sub.sub_service.price
                sub.paid_amount = price
                sub.save()

                # Optional: log to LegalPayout or another model
                LegalOfficePayout.objects.create(
                    client_sub_service=sub,
                    amount_paid=price,
                    paid_month=payout_date,
                    added_by=request.user
                )

        messages.success(request, f"Payout successful for {len(subservices)} sub-services.")
        return redirect('accounts_dashboard')