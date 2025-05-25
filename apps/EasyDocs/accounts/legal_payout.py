# services/legal_payout.py

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Sum, F
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import View
import json
from apps.EasyDocs.models import LegalOfficePayout, ClientSubService, SubService


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








class BulkPayToLegalView(View):
    def post(self, request, *args, **kwargs):
        try:
            data = json.loads(request.body)
            ids = data.get('subservice_ids', [])
        except json.JSONDecodeError:
            return JsonResponse({'status': 'error', 'message': 'Invalid JSON.'}, status=400)

        if not ids:
            return JsonResponse({'status': 'error', 'message': '❌ No subservices selected.'}, status=400)

        # Step 1: Fetch unpaid subservices
        subs_qs = ClientSubService.objects.filter(
            id__in=ids,
            is_paid_to_legal_office=False
        )

        count = subs_qs.count()
        if count == 0:
            return JsonResponse({
                'status': 'error',
                'message': '❌ No valid unpaid legal subservices found in your selection.'
            }, status=400)

        # Step 2: Calculate total
        total = subs_qs.aggregate(total=Sum('paid_amount'))['total'] or Decimal('0.00')

        # Step 3: Mark as paid
        subs_qs.update(is_paid_to_legal_office=True)

        # Step 4: Re-fetch the fresh queryset
        subservices_to_add = list(ClientSubService.objects.filter(id__in=ids))

        # Step 5: Create or update monthly payout
        month_start = timezone.now().date().replace(day=1)
        payout, created = LegalOfficePayout.objects.get_or_create(
            month=month_start,
            defaults={'total_amount': total}
        )

        if not created:
            payout.total_amount = F('total_amount') + total
            payout.save()
            payout.refresh_from_db()  # ✅ Important after F()

        # Step 6: Link subservices
        before_count = payout.subservices.count()
        print("Before linking:", before_count)

        payout.subservices.add(*subservices_to_add)

        after_count = payout.subservices.count()
        print("After linking:", after_count)
        print("Linked Subservices:", list(payout.subservices.values_list('id', flat=True)))

        return JsonResponse({
            'status': 'success',
            'updated_count': count,
            'total_paid': f"{total:.2f}",
            'linked_before': before_count,
            'linked_after': after_count
        })










# class BulkPayoutView(View):
#     def post(self, request, *args, **kwargs):
#         subservice_ids = request.POST.getlist('subservices')
#         paid_month = request.POST.get('paid_month')
#
#         if not subservice_ids:
#             messages.error(request, "No sub-services selected.")
#             return redirect('accounts_dashboard')
#
#         if not paid_month:
#             messages.error(request, "Please select a payout month.")
#             return redirect('accounts_dashboard')
#
#         try:
#             payout_date = parse_date(paid_month + "-01")  # Normalize to first of month
#         except Exception:
#             messages.error(request, "Invalid payout month.")
#             return redirect('accounts_dashboard')
#
#         subservices = ClientSubService.objects.filter(id__in=subservice_ids)
#
#         with transaction.atomic():
#             for sub in subservices:
#                 # Example logic: only pay if balance remains
#                 if sub.overridden_price and sub.paid_amount >= sub.overridden_price:
#                     continue  # already fully paid
#
#                 price = sub.overridden_price or sub.sub_service.price
#                 sub.paid_amount = price
#                 sub.save()
#
#                 # Optional: log to LegalPayout or another model
#                 LegalOfficePayout.objects.create(
#                     client_sub_service=sub,
#                     amount_paid=price,
#                     paid_month=payout_date,
#                     added_by=request.user
#                 )
#
#         messages.success(request, f"Payout successful for {len(subservices)} sub-services.")
#         return redirect('accounts_dashboard')