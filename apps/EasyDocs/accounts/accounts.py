# clients/utils.py
from decimal import ROUND_HALF_UP, InvalidOperation, Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse

from apps.EasyDocs.forms import ExpenseForm
from apps.EasyDocs.models import ClientServiceProcess, Payment, Expense, ClientSubService, SubService

from django.db.models import Sum, Value, DecimalField, ExpressionWrapper, F, When, Case
from django.db.models.functions import Coalesce

from apps.EasyDocs.models import ClientService

from django.views.generic import TemplateView, View, FormView
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.contrib import messages
from django.utils import timezone

from apps.EasyDocs.forms import LegalPayoutForm
from apps.EasyDocs.models import Expense, ClientSubService
from apps.EasyDocs.accounts.legal_payout import create_legal_payout

import logging

logger = logging.getLogger(__name__)


def get_payment_context(client, service_id=None):
    """
    Returns context for rendering a payment UI for a client.
    If service_id is None: returns a list of the client's services.
    If service_id is provided: returns detailed data for that ClientService,
    including processes, balances, and the payment-submit URL.
    """
    context = {
        'client': client,
        'services': [],  # list of {id, name, total_balance}
        'selected_service': None,
        'processes': [],  # list of {name, cost, paid, pending}
        'payment_url': None,  # endpoint to POST a payment
    }

    # 1. Populate the list of services
    client_services = ClientService.objects.filter(client=client).select_related('service')
    for cs in client_services:
        context['services'].append({
            'id': cs.id,
            'name': cs.service.name,
            'total_balance': cs.total_balance(),
        })

    if service_id:
        # 2. Fetch that specific ClientService
        cs = get_object_or_404(ClientService, pk=service_id, client=client)
        context['selected_service'] = {
            'id': cs.id,
            'name': cs.service.name,
            'total_price': cs.service.total_price,
            'total_paid': cs.total_paid(),
            'total_balance': cs.total_balance(),
        }

        # 3. List its processes in order
        csps = ClientServiceProcess.objects.filter(
            client_service=cs
        ).select_related('process').order_by('process__step_order')

        for csp in csps:
            context['processes'].append({
                'name': csp.process.name,
                'cost': csp.process.cost,
                'paid': csp.total_paid,
                'pending': csp.pending_amount,
                'status': csp.status,
            })

        # 4. URL to POST the payment (you’d wire this up in urls.py)
        context['payment_url'] = reverse('clients:make_payment', args=[cs.id])

    return context


#
#
# def payment_context(request, pk):
#     """
#     AJAX: returns JSON context for the payment modal.
#     Accepts optional GET param ?service_id=123
#     """
#     service_id = request.GET.get('service_id')
#     context = get_payment_context(request.user.client, service_id)
#     return JsonResponse(context)
#
# @require_POST
# def make_payment(request, cs_id):
#     """
#     Handles the actual payment POST.
#     Expects 'amount' and 'payment_method' in POST.
#     """
#     cs = get_object_or_404(ClientService, pk=cs_id, client=request.user.client)
#     amount = request.POST.get('amount')
#     method = request.POST.get('payment_method')
#
#     # Create the payment — your Payment.save() does the allocation
#     Payment.objects.create(
#         client_service=cs,
#         amount=amount,
#         payment_method=method,
#         transaction_id=request.POST.get('transaction_id', None)
#     )
#
#     return JsonResponse({'status': 'success'})


# apps/EasyDocs/accounts.py


def add_payment_to_client_service(
        client_service_id, amount, payment_method, transaction_id=None
):
    try:
        client_service = ClientService.objects.get(id=client_service_id)
    except ClientService.DoesNotExist:
        return {'success': False, 'error': 'Client Service not found.'}

    # Ensure amount is Decimal
    amount = Decimal(str(amount))

    payment = Payment.objects.create(
        client_service=client_service,
        amount=amount,
        payment_method=payment_method,
        transaction_id=transaction_id or '',
        payment_date=timezone.now()
    )

    return {
        'success': True,
        'payment': payment,
        # total_paid is a method, so call it
        'total_paid': client_service.total_paid(),
        # pending_balance uses total_balance()
        'pending_balance': client_service.total_balance(),
    }


def add_payment_view(request, client_id):
    if request.method == 'POST':
        client_service_id = request.POST.get('client_service_id')
        raw_amount = request.POST.get('amount')
        payment_method = request.POST.get('payment_method')
        transaction_id = request.POST.get('transaction_id', '')

        try:
            client_service = get_object_or_404(ClientService, id=client_service_id)

            # Precise rounding
            amount = Decimal(raw_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            payment = Payment(
                client_service=client_service,
                amount=amount,
                payment_method=payment_method,
                transaction_id=transaction_id or None
            )

            payment.full_clean()  # runs your `clean()`
            payment.save()

            messages.success(request, f"✅ Payment of KES {amount:.2f} recorded successfully.")

        except ValidationError as ve:
            messages.error(request, f"❌ {ve.messages[0]}")
        except (InvalidOperation, ValueError):
            messages.error(request, "❌ Invalid amount entered. Please enter a valid number.")
        except Exception as e:
            logger.exception(
                "Error in add_payment_view (client_id=%s, cs_id=%s): %s",
                client_id, client_service_id, e
            )
            messages.error(request, f"❌ Unexpected error: {e}")

    return redirect('client_details', client_id=client_id)


from collections import defaultdict


def get_client_payment_history(client_id):
    """
    Returns a list of dicts, one per ClientService for this client,
    each with total_amount, total_paid, pending_balance,
    a chronological payment_breakdown list, and allocations.
    """
    try:
        # Annotate only total_paid; we'll use cs.total_balance below
        services = (
            ClientService.objects
            .filter(client_id=client_id)
            .select_related('service')
            .annotate(
                total_paid=Coalesce(
                    Sum('payments__amount'),
                    Value(0),
                    output_field=DecimalField()
                )
            )
        )
    except Exception:
        logger.exception("Failed to load ClientService for client %s", client_id)
        return []

    history = []
    for cs in services:
        # Fetch payments & history in proper order, crushing N+1s
        payments = cs.payments.all().order_by('payment_date', 'id')
        allocations = (
            cs.payment_history
            .select_related('service_process__process',
                            'sub_service__sub_service')
            .order_by('created_at')
        )

        # Build the payment ledger with running balance
        ledger = []
        running_paid = Decimal('0.00')
        for p in payments:
            running_paid += p.amount
            ledger.append({
                'date': p.payment_date.strftime('%Y-%m-%d'),
                'amount': str(p.amount),
                'method': p.payment_method,
                'reference': p.transaction_id or '',
                'remaining_balance': str(cs.full_total_price - running_paid),
            })

        # Build allocation entries with running balance
        alloc_list = []
        running_allocated = Decimal('0.00')
        for h in allocations:
            amt = h.amount or Decimal('0.00')
            running_allocated += amt
            remaining_after = cs.full_total_price - running_allocated

            if h.reason == 'service_step' and h.service_process:
                proc = h.service_process
                name = proc.process.name
                paid = proc.paid_amount
                cost = proc.cost
                status = 'Fully Paid' if paid >= cost else 'Partially Paid'
                alloc_list.append({
                    'type': 'step',
                    'name': name,
                    'amount': str(amt),
                    'remaining_balance': str(remaining_after),
                    'status': status,
                    'order': proc.process.step_order,
                })

            elif h.reason == 'sub_service' and h.sub_service:
                sub = h.sub_service
                name = sub.sub_service.name
                paid = sub.paid_amount
                cost = sub.price
                status = 'Fully Paid' if paid >= cost else 'Partially Paid'
                alloc_list.append({
                    'type': 'sub',
                    'name': name,
                    'amount': str(amt),
                    'remaining_balance': str(remaining_after),
                    'status': status,
                    'order': h.created_at,
                })

            else:
                alloc_list.append({
                    'type': 'other',
                    'name': h.get_reason_display(),
                    'amount': str(amt),
                    'remaining_balance': str(remaining_after),
                    'status': '',
                    'order': h.created_at,
                })

        # Sort allocations: steps by order, then others by timestamp
        steps = sorted([a for a in alloc_list if a['type'] == 'step'], key=lambda x: x['order'])
        subs = sorted([a for a in alloc_list if a['type'] != 'step'], key=lambda x: x['order'])
        ordered_allocs = steps + subs

        history.append({
            'service_id': cs.id,
            'service_label': f"{cs.service.name} — {cs.land_description}",
            'total_amount': str(cs.full_total_price),
            'total_paid': str(cs.total_paid),
            'pending_balance': str(cs.total_balance),  # now using model property
            'payment_status': (
                'Fully Paid' if cs.total_balance <= 0 else
                'Partially Paid' if cs.total_paid > 0 else
                'Not Paid'
            ),
            'payment_breakdown': ledger,
            'allocations': ordered_allocs,
        })

    return history


class ExpenseView(View):
    def post(self, request, *args, **kwargs):
        pk = request.POST.get("expense_id")
        instance = Expense.objects.filter(pk=pk).first() if pk else None
        form = ExpenseForm(request.POST, instance=instance)

        if form.is_valid():
            form.save()
            messages.success(request, f"Expense {'updated' if pk else 'added'} successfully.")
        else:
            messages.error(request, "Failed to submit expense. Please fix the errors.")

        return redirect(request.META.get("HTTP_REFERER", "/accounts/"))


def get_all_payment_history():
    return (
        Payment.objects
        .select_related('client_service__client')
        .order_by('-payment_date')
    )


from collections import defaultdict
from decimal import Decimal
from django.utils.timezone import now


def get_subservice_summary():
    current_year = now().year
    subservices = ClientSubService.objects.select_related(
        'client_service__client', 'sub_service'
    ).filter(added_on__year=current_year)

    total_price = Decimal('0.00')
    total_paid = Decimal('0.00')
    by_client = defaultdict(lambda: {'total': Decimal('0.00'), 'paid': Decimal('0.00')})
    by_month = defaultdict(lambda: Decimal('0.00'))  # optionally keyed by (month, dept)

    for ss in subservices:
        price = ss.price
        paid = ss.paid_amount
        client_name = ss.client_service.client.first_name + ' ' + ss.client_service.client.last_name
        month = ss.added_on.strftime('%B')  # e.g., "April"
        dept = ss.sub_service.department  # assuming department is a field

        # Global sums
        total_price += price
        total_paid += paid

        # By client
        by_client[client_name]['total'] += price
        by_client[client_name]['paid'] += paid

        # By month-dept (optional detailed breakdown)
        by_month[(month, dept)] += price

    summary = {
        'total_price': total_price,
        'total_paid': total_paid,
        'total_balance': total_price - total_paid,
        'by_client': [
            {
                'client': client,
                'total': data['total'],
                'paid': data['paid'],
                'balance': data['total'] - data['paid']
            }
            for client, data in by_client.items()
        ],
        'by_month': by_month  # optional for charts or deeper breakdown
    }

    return summary


# views.py


class AccountsDashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'Management/accounts.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        qs = ClientSubService.objects.annotate(
            annotated_price=Coalesce(F('overridden_price'), F('sub_service__price')),
            annotated_balance=ExpressionWrapper(
                Coalesce(F('overridden_price'), F('sub_service__price')) - F('paid_amount'),
                output_field=DecimalField()
            )
        )

        ctx['summary'] = self.compute_summary(qs)
        ctx['expenses'] = Expense.objects.all().order_by('-date')
        ctx['client_payments'] = get_all_payment_history()
        ctx['form'] = ExpenseForm()
        ctx['users'] = User.objects.all()
        return ctx

    def compute_summary(self, qs):
        total_price = sum(css.annotated_price for css in qs)
        total_paid = sum(css.paid_amount for css in qs)
        total_balance = sum(css.annotated_balance for css in qs)
        return {
            'total_price': f"{total_price:.2f}",
            'total_paid': f"{total_paid:.2f}",
            'total_balance': f"{total_balance:.2f}"
        }







class SubServiceFilterView(View):
    def get(self, request):
        start = request.GET.get('start_date')
        end = request.GET.get('end_date')
        qs = ClientSubService.objects.select_related(
            'client_service__client', 'sub_service'
        ).order_by('-added_on')
        if start:
            qs = qs.filter(added_on__date__gte=start)
        if end:
            qs = qs.filter(added_on__date__lte=end)
        summary = AccountsDashboardView().compute_summary(qs)
        return render(request, 'Management/partials/_subservices_table.html', {
            'sub_services': qs,
            'summary': summary
        })


class LegalPayoutCreateView(FormView):
    form_class = LegalPayoutForm
    success_url = '/management/accounts/'  # or name‑reverse

    def form_valid(self, form):
        try:
            payout = create_legal_payout(
                form.cleaned_data['subservices'],
                form.cleaned_data['paid_month']
            )
            messages.success(
                self.request,
                f"✅ Created payout for {payout.paid_month:%B %Y}, total KES {payout.total_amount:.2f}."
            )
            return JsonResponse({'success': True})
        except Exception as e:
            messages.error(self.request, f"❌ Could not create payout: {e}")
            return JsonResponse(
                {'success': False, 'html': self.get_form_html(form)},
                status=400
            )

    def form_invalid(self, form):
        return JsonResponse(
            {'success': False, 'html': self.get_form_html(form)},
            status=400
        )

    def get_form_html(self, form):
        # render just the form inside the modal
        return render(self.request, 'Management/partials/_legal_payout_form.html', {
            'form': form
        }).content.decode()


def expense_delete(request, pk):
    exp = get_object_or_404(Expense, pk=pk)

    if request.method == 'POST':
        exp.delete()
        messages.success(request, "Expense deleted successfully.")
        return redirect(request.META.get('HTTP_REFERER', 'expense_list'))

    # If not POST, render confirmation (or redirect to prevent blank page)
    messages.warning(request, "Invalid delete request.")
    return redirect(request.META.get('HTTP_REFERER', 'expense_list'))
