# clients/utils.py
from decimal import Decimal, ROUND_HALF_UP

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from .forms import ExpenseForm
from .models import ClientService, ClientServiceProcess, Service, Payment, Expense

from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .models import ClientService, PaymentHistory


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

def get_client_payment_history(client_id, start_date=None, end_date=None, service_id=None):
    # 1) Fetch only the ClientService records for this client
    client_services = ClientService.objects.filter(client_id=client_id)

    # 2) If service_id is provided, narrow it down to that one service
    if service_id:
        client_services = client_services.filter(id=service_id)

    # 3) Build a lookup map of those services
    client_services = client_services.select_related('service')
    cs_map = {cs.id: cs for cs in client_services}

    # 4) Query PaymentHistory for only those services
    qs = PaymentHistory.objects.select_related('payment').filter(
        client_service__in=cs_map.keys()
    )

    # 5) Apply date filters on Payment (not on History itself)
    if start_date:
        qs = qs.filter(payment__payment_date__gte=start_date)
    if end_date:
        qs = qs.filter(payment__payment_date__lte=end_date)

    # 6) Order and serialize
    qs = qs.order_by('-payment__payment_date')

    result = []
    for h in qs:
        cs = cs_map[h.client_service_id]
        payment = h.payment

        result.append({
            'service_id': cs.id,
            'service_name': f"{cs.service.name} for Plot {cs.land_description}",
            'amount': str(h.amount),
            'method': payment.payment_method if payment else 'N/A',
            'reason': h.get_reason_display() if h.reason else '',
            'timestamp': payment.payment_date.strftime('%m/%d/%Y %H:%M') if payment else 'N/A',
            'payment_status': cs.payment_status,
        })

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



class AccountsDashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'Management/accounts.html'
    # login_url, redirect_field_name etc. can be configured if needed

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # 1. All expenses
        ctx['expenses'] = Expense.objects.all().order_by('-date')
        ctx['payment_history'] = (
            PaymentHistory.objects
            .select_related('payment', 'client_service__client', 'service_process', 'sub_service')
            .order_by('-created_at')
        )

        ctx['form'] = ExpenseForm()  # only needed for rendering empty modal
        ctx['users'] = User.objects.all()  # needed for select options

        return ctx





def expense_delete(request, pk):
    exp = get_object_or_404(Expense, pk=pk)

    if request.method == 'POST':
        exp.delete()
        messages.success(request, "Expense deleted successfully.")
        return redirect(request.META.get('HTTP_REFERER', 'expense_list'))

    # If not POST, render confirmation (or redirect to prevent blank page)
    messages.warning(request, "Invalid delete request.")
    return redirect(request.META.get('HTTP_REFERER', 'expense_list'))