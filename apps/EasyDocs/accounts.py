# clients/utils.py
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models.functions import TruncMonth
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from .forms import ExpenseForm
from .models import ClientService, ClientServiceProcess, Service, Payment, Expense, ClientSubService

from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .models import ClientService, PaymentHistory
import logging
from decimal import Decimal
from django.db.models import Sum

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
        amount = request.POST.get('amount')
        payment_method = request.POST.get('payment_method')
        transaction_id = request.POST.get('transaction_id', '')

        try:
            client_service = get_object_or_404(ClientService, id=client_service_id)

            # Use Decimal for precise comparison
            amount = Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            payment = Payment(
                client_service=client_service,
                amount=amount,
                payment_method=payment_method,
                transaction_id=transaction_id or None
            )

            payment.full_clean()  # This will call clean() and raise ValidationError if overpaid
            payment.save()

            messages.success(request, f"✅ Payment of KES {amount:.2f} recorded successfully.")

        except ValidationError as ve:
            # Display user-friendly validation error
            messages.error(request, f"❌ {ve.messages[0]}")
        except (ValueError, InvalidOperation):
            messages.error(request, "❌ Invalid amount entered. Please enter a valid number.")
        except Exception as e:
            messages.error(request, "❌ An unexpected error occurred. Please try again later.")

    return redirect('client_details', client_id=client_id)

from collections import defaultdict




def get_client_payment_history(client_id):
    try:
        client_services = (
            ClientService.objects
            .filter(client_id=client_id)
            .select_related('service')
        )
        cs_map = {cs.id: cs for cs in client_services}
    except Exception as e:
        logger.error(f"[get_client_payment_history] Error loading services for client {client_id}: {e}", exc_info=True)
        return []

    # Load related PaymentHistory entries
    try:
        histories_qs = (
            PaymentHistory.objects
            .select_related('payment', 'service_process', 'sub_service')
            .filter(client_service__in=cs_map.keys())
        )
    except Exception as e:
        logger.error(f"[get_client_payment_history] Error loading PaymentHistory for client {client_id}: {e}",
                     exc_info=True)
        return []

    # Load payments directly
    try:
        payments_qs = (
            Payment.objects
            .filter(client_service__in=cs_map.keys())
            .order_by('payment_date', 'id')
        )
    except Exception as e:
        logger.error(f"[get_client_payment_history] Error loading Payments for client {client_id}: {e}", exc_info=True)
        return []

    aggregated = {}

    # Step 1: Use Payment to build the payment breakdown
    for p in payments_qs:
        cs = cs_map.get(p.client_service_id)
        if not cs:
            continue
        sid = cs.id
        if sid not in aggregated:
            aggregated[sid] = {
                'id': cs.id,
                'service_name': f"{cs.service.name} for Plot {cs.land_description}",
                'total_amount': float(cs.effective_total_price),
                'total_paid': 0.0,
                'payment_breakdown': [],
                'allocations': [],
            }

        amt = float(p.amount or 0)
        aggregated[sid]['total_paid'] += amt
        remaining = aggregated[sid]['total_amount'] - aggregated[sid]['total_paid']

        aggregated[sid]['payment_breakdown'].append({
            'payment_date': p.payment_date.strftime('%d-%m-%y'),
            'amount_paid': amt,
            'method': p.payment_method,
            'reference': p.transaction_id or 'N/A',
            'remaining_balance': remaining,
        })

    # Step 2: Use PaymentHistory for allocation breakdown
    for h in histories_qs:
        cs = cs_map.get(h.client_service_id)
        if not cs:
            continue
        sid = cs.id
        amt = float(h.amount or 0)

        if h.reason == 'service_step' and h.service_process:
            proc = h.service_process
            name = proc.process.name
            paid = float(proc.paid_amount)
            cost = float(proc.cost)
            status = 'Fully Paid' if paid >= cost else 'Partially Paid'
            alloc = {
                'reason': name,
                'amount': amt,
                'status': status,
                'step_order': proc.process.step_order,
            }
        elif h.reason == 'sub_service' and h.sub_service:
            sub = h.sub_service
            name = sub.sub_service.name
            paid = float(sub.paid_amount)
            cost = float(sub.price)
            status = 'Fully Paid' if paid >= cost else 'Partially Paid'
            alloc = {
                'reason': name,
                'amount': amt,
                'status': status,
                'added_on': h.created_at,
            }
        else:
            alloc = {
                'reason': h.get_reason_display() or 'Unknown',
                'amount': amt,
                'status': '',
                'added_on': h.created_at,
            }

        aggregated[sid]['allocations'].append(alloc)

    # Step 3: Finalize results
    result = []
    for sid, data in aggregated.items():
        procs = [a for a in data['allocations'] if 'step_order' in a]
        procs.sort(key=lambda x: x['step_order'])
        subs = [a for a in data['allocations'] if 'step_order' not in a]
        subs.sort(key=lambda x: x['added_on'])
        data['allocations'] = procs + subs

        data['pending_balance'] = data['total_amount'] - data['total_paid']
        data['payment_status'] = (
            'Fully Paid' if data['total_paid'] >= data['total_amount']
            else 'Partially Paid'
        )

        result.append(data)

    return result


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


class AccountsDashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'Management/accounts.html'
    # login_url, redirect_field_name etc. can be configured if needed

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # 1. All expenses
        ctx['expenses'] = Expense.objects.all().order_by('-date')

        now = timezone.now()
        first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        ctx['sub_services'] = (ClientSubService.objects.select_related('client_service__client', 'sub_service')
                               .order_by('-added_on'))
        # summary for initial month
        ctx['summary'] = self.compute_summary(ctx['sub_services'])

        ctx['client_payments'] = get_all_payment_history()

        ctx['form'] = ExpenseForm()  # only needed for rendering empty modal
        ctx['users'] = User.objects.all()  # needed for select options

        return ctx

    def get(self, request, *args, **kwargs):
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':


            # parse dates
            start = request.GET.get('start_date')
            end = request.GET.get('end_date')
            qs = ClientSubService.objects.select_related('client_service__client', 'sub_service')
            if start:
                qs = qs.filter(added_on__date__gte=start)
            if end:
                qs = qs.filter(added_on__date__lte=end)
            qs = qs.order_by('-added_on')
            # build JSON response
            summary = self.compute_summary(qs)
            rows = []
            for css in qs:
                rows.append({
                    'added_on': css.added_on.strftime('%Y-%m-%d %H:%M'),
                    'client': str(css.client_service.client),
                    'sub_service': css.sub_service.name,
                    'price': float(css.price),
                    'paid': float(css.paid_amount),
                    'balance': float(css.balance),
                    'status': 'Fully Paid' if css.balance <= 0 else 'Pending'
                })
            return JsonResponse({'summary': summary, 'rows': rows})
        return super().get(request, *args, **kwargs)

    def compute_summary(self, qs):
        total_price = sum(css.price for css in qs)
        total_paid = sum(css.paid_amount for css in qs)
        total_balance = total_price - total_paid
        return {
            'total_price': f"{total_price:.2f}",
            'total_paid': f"{total_paid:.2f}",
            'total_balance': f"{total_balance:.2f}"
        }





def expense_delete(request, pk):
    exp = get_object_or_404(Expense, pk=pk)

    if request.method == 'POST':
        exp.delete()
        messages.success(request, "Expense deleted successfully.")
        return redirect(request.META.get('HTTP_REFERER', 'expense_list'))

    # If not POST, render confirmation (or redirect to prevent blank page)
    messages.warning(request, "Invalid delete request.")
    return redirect(request.META.get('HTTP_REFERER', 'expense_list'))