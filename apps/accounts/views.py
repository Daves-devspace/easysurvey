# apps/accounts/views.py
from django.shortcuts import redirect, render
from django.contrib import messages
import logging
from django.views.generic import TemplateView, View
from django.utils import timezone
from django.db.models import Sum
from django.db import IntegrityError, transaction
from decimal import Decimal
from django.http import JsonResponse, HttpResponseRedirect
from django.urls import reverse
from apps.accounts.forms import OpeningBalanceForm, InstitutionPayoutForm
from apps.accounts.models import CashbookEntry
from apps.EasyDocs.forms import ExpenseForm
from apps.EasyDocs.models import Expense
from apps.accounts.services.opening_balance import (
    get_opening_summary,
    persist_flagged_opening,
    add_opening_contribution,
    replace_flagged_snapshot,
    log_audit
)
from apps.accounts.tasks import reconcile_flagged_opening_task  

from apps.accounts.services.cashbook import record_cash_out_institution
logger = logging.getLogger(__name__)

from django.views import View
from django.http import JsonResponse
from django.utils import timezone
from decimal import Decimal
from apps.accounts.services.opening_balance import compute_latest_carried_balance, replace_flagged_snapshot, log_audit

class SyncOpeningBalanceView(View):
    """
    POST endpoint to sync the flagged opening balance for a given date.
    Updates the flagged entry directly using delta-based adjustment.
    Returns a clean JSON with the current flagged balance and delta.
    """

    def post(self, request, *args, **kwargs):
        user = request.user if request.user.is_authenticated else None
        date_str = request.POST.get("date")

        if not date_str:
            return JsonResponse({"success": False, "message": "Missing date"}, status=400)

        try:
            entry_date = timezone.datetime.strptime(date_str, "%Y-%m-%d").date()
        except Exception:
            entry_date = timezone.now().date()

        # Compute expected carried balance
        expected = compute_latest_carried_balance(entry_date)

        # Update flagged opening balance directly using delta-based adjustment
        flagged, delta, _ = replace_flagged_snapshot(entry_date, Decimal(expected), user=user)

        # Build a clean response for AJAX/UI
        payload = {
            "success": True,
            "entry_date": entry_date.isoformat(),
            "flagged_balance": float(flagged.balance_after),
            "delta": float(delta),
            "message": "Opening balance synced successfully!" if delta != 0 else "Opening already in sync ✅",
        }

        return JsonResponse(payload)


class CheckOpeningSyncView(View):
    """
    GET /api/check-opening-sync/?date=YYYY-MM-DD
    Returns JSON:
    {
        "in_sync": bool,
        "flagged_balance": float,
        "expected_balance": float,
        "delta": float
    }
    """
    def get(self, request):
        date_str = request.GET.get("date")
        if not date_str:
            return JsonResponse({"success": False, "message": "Missing date"}, status=400)

        try:
            entry_date = timezone.datetime.strptime(date_str, "%Y-%m-%d").date()
        except Exception:
            return JsonResponse({"success": False, "message": "Invalid date"}, status=400)

        summary = get_opening_summary(entry_date)
        flagged_balance = summary["flagged"]
        expected_balance = compute_latest_carried_balance(entry_date)
        delta = expected_balance - flagged_balance

        return JsonResponse({
            "in_sync": delta == 0,
            "flagged_balance": float(flagged_balance),
            "expected_balance": float(expected_balance),
            "delta": float(delta),
        })


class CashbookDashboardView(TemplateView):
    template_name = "Accounts/cash_in_out.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # allow date switching from query param
        date_str = self.request.GET.get("date")
        if date_str:
            try:
                selected_date = timezone.datetime.strptime(date_str, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                selected_date = timezone.now().date()
        else:
            selected_date = timezone.now().date()

        # 🔹 opening summary (flagged + contributions + total)
        opening_summary = get_opening_summary(selected_date)

        # 🔹 daily in/out
        today_in = (
            CashbookEntry.objects.filter(entry_type="IN", created_at__date=selected_date)
            .aggregate(Sum("amount"))["amount__sum"] or Decimal("0.00")
        )
        today_out = (
            CashbookEntry.objects.filter(entry_type="OUT", created_at__date=selected_date)
            .aggregate(Sum("amount"))["amount__sum"] or Decimal("0.00")
        )

        # 🔹 closing balance (total opening + inflows - outflows)
        closing_balance = opening_summary["flagged"] + today_in - today_out

        # 🔹 extra info
        recent_entries = CashbookEntry.objects.filter(created_at__date=selected_date).order_by("-created_at")
        expenses = Expense.objects.all().order_by("-date")

        # 🔹 cashflow ratio
        cash_flow_ratio = None
        ratio_display = "N/A"
        ratio_text = ""
        if today_out and today_out != 0:
            cash_flow_ratio = float(today_in) / float(today_out)
            # Display as whole number if ratio >= 1, else show fraction or decimal
            if cash_flow_ratio >= 1:
                ratio_display = f"{int(round(cash_flow_ratio))}×"
            else:
                ratio_display = f"{cash_flow_ratio:.2f}×"
            # Optional small text for more info
            if cash_flow_ratio > 1:
                ratio_text = f"Income is {ratio_display} of expenses"
            elif cash_flow_ratio == 1:
                ratio_text = "Income equals expenses"
            else:
                ratio_text = f"Income is only {ratio_display} of expenses"


        context.update({
            "today": selected_date,
            "opening_summary": opening_summary,   # ✅ one dict with all opening info
            "today_in": today_in,
            "today_out": today_out,
            "closing_balance": closing_balance,
            "recent_entries": recent_entries,
            "expenses": expenses,
            "form": ExpenseForm(),
            "payout_form": InstitutionPayoutForm(),
            "opening_balance_form": OpeningBalanceForm(initial={"date": selected_date}),
            "cash_flow_ratio": cash_flow_ratio,
            "ratio_display": ratio_display,
            "is_positive_flow": cash_flow_ratio and cash_flow_ratio >= 1 if cash_flow_ratio else True
        })
        return context

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        if request.headers.get("HX-Request"):
            return render(request, "Accounts/partials/cashbook_dashboard_content.html", context)
        return self.render_to_response(context)


class RecordInstitutionPayoutView(View):
    def post(self, request, *args, **kwargs):
        form = InstitutionPayoutForm(request.POST)
        if form.is_valid():
            amount = form.cleaned_data["amount"]
            description = form.cleaned_data["description"] or "Institution payout (cash out)"
            try:
                record_cash_out_institution(amount, request.user, description)
                messages.success(request, f"Institution payout of {amount} recorded.")
            except ValueError as e:
                messages.error(request, str(e))
        else:
            messages.error(request, "Error recording payout. Please check the form.")
        return redirect("cashbook_dashboard")




class CheckOpeningBalanceView(View):
    """
    GET /api/check-opening-balance/?date=YYYY-MM-DD
    Returns JSON with:
    {
      flagged_exists: bool,
      flagged_balance: float,
      contributions_total: float,
      total_opening: float,
      flagged_entry_id: int|null,
      flagged_description: str
    }
    """
    def get(self, request):
        date_str = request.GET.get("date")
        if not date_str:
            return JsonResponse({"error": "missing date"}, status=400)

        try:
            entry_date = timezone.datetime.strptime(date_str, "%Y-%m-%d").date()
        except Exception:
            return JsonResponse({"error": "invalid date"}, status=400)

        summary = get_opening_summary(entry_date)
        flagged_entry = summary.get("flagged_entry")

        return JsonResponse({
            "flagged_exists": summary["flagged_exists"],
            "flagged_balance": float(summary["flagged_balance"]),
            "contributions_total": float(summary["contributions_total"]),
            "total_opening": float(summary["total_opening"]),
            "flagged_entry_id": getattr(flagged_entry, "pk", None),
            "flagged_description": getattr(flagged_entry, "description", "") if flagged_entry else "",
        })






class OpeningBalanceCreateView(View):
    """
    Create opening contribution with optional immediate flagged snapshot adjustment
    (Option A workflow: single adjustment + contribution).
    """
    def post(self, request, *args, **kwargs):
        form = OpeningBalanceForm(request.POST)
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        if not form.is_valid():
            if is_ajax:
                return JsonResponse({"success": False, "errors": form.errors}, status=400)
            messages.error(request, "Invalid opening balance form.")
            return HttpResponseRedirect(request.META.get("HTTP_REFERER", reverse("cashbook_dashboard")))

        today = timezone.now().date()
        amount = Decimal(form.cleaned_data["amount"])
        description = form.cleaned_data.get("description", "").strip()
        user = request.user if request.user.is_authenticated else None

        if amount <= Decimal("0.00"):
            msg = "Amount must be greater than 0 to add a contribution."
            if is_ajax:
                return JsonResponse({"success": False, "message": msg}, status=400)
            messages.error(request, msg)
            return HttpResponseRedirect(request.META.get("HTTP_REFERER", reverse("cashbook_dashboard")))

        try:
            with transaction.atomic():
                # ✅ Ensure flagged opening exists
                flagged, created, carried_balance = persist_flagged_opening(today, user=user)

                # ✅ Detect mismatch and adjust immediately if needed
                delta = Decimal(flagged.balance_after) - carried_balance
                adjustment_entry = None
                if Decimal(flagged.balance_after) != Decimal(carried_balance):
                    flagged, delta, adjustment_entry = replace_flagged_snapshot(today, carried_balance, user=user)

                # ✅ Add contribution on top of adjusted balance
                contribution = add_opening_contribution(today, amount, description or None, user=user)

                # Build response summary
                summary = get_opening_summary(today)
                payload = {
                    "success": True,
                    "action": "contribution_created",
                    "entry_id": contribution.pk,
                    "entry_amount": float(contribution.amount),
                    "entry_balance_after": float(contribution.balance_after),
                    "contributions_total": float(summary["contributions_total"]),
                    "total_opening": float(summary["total_opening"]),
                    "message": f"Added {amount} to opening balance for {today}.",
                }
                if adjustment_entry:
                    payload["adjustment_id"] = adjustment_entry.pk
                    payload["adjustment_amount"] = float(adjustment_entry.amount)
                    payload["adjustment_message"] = f"Adjustment of {adjustment_entry.amount} created to align flagged opening."

                if is_ajax:
                    return JsonResponse(payload)
                messages.success(request, payload["message"])
                return HttpResponseRedirect(request.META.get("HTTP_REFERER", reverse("cashbook_dashboard")))

        except Exception as exc:
            logger.exception("Failed to add opening contribution: %s", exc)
            msg = f"Error adding contribution: {exc}"
            if is_ajax:
                return JsonResponse({"success": False, "message": msg}, status=500)
            messages.error(request, msg)
            return HttpResponseRedirect(request.META.get("HTTP_REFERER", reverse("cashbook_dashboard")))
