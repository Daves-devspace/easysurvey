# clients/utils.py
from decimal import Decimal

from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone

from .models import ClientService, ClientServiceProcess, Service, Payment

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
            amount = float(amount)
        except (ValueError, TypeError):
            # Handle invalid amount
            return redirect('client_details', client_id=client_id)

        result = add_payment_to_client_service(
            client_service_id=client_service_id,
            amount=amount,
            payment_method=payment_method,
            transaction_id=transaction_id
        )

        if result['success']:
            # Optionally: set a success message here
            pass
        else:
            # Handle error
            pass

    return redirect('client_details', client_id=client_id)


from django.db.models import Prefetch

def get_client_payment_history(client_id, start_date=None, end_date=None, service_id=None):
    """
    Returns a flat list of all payment history entries for a given client,
    optionally filtered by date range and/or service.
    Each payment includes service info and payment status.
    """
    # Fetch only relevant client services
    client_services = ClientService.objects.filter(client_id=client_id)

    if service_id:
        client_services = client_services.filter(id=service_id)

    # Prefetch related service and attach to a dict for fast lookup
    client_services = client_services.select_related('service')
    client_services_dict = {cs.id: cs for cs in client_services}

    # Get related payment histories
    histories = PaymentHistory.objects.filter(client_service__in=client_services_dict.keys())

    if start_date:
        histories = histories.filter(timestamp__gte=start_date)
    if end_date:
        histories = histories.filter(timestamp__lte=end_date)

    # Order by latest payment first
    histories = histories.order_by('-timestamp')

    # Flattened list of payments with status
    payment_data = []
    for h in histories:
        cs = client_services_dict[h.client_service_id]

        payment_data.append({
            'timestamp': h.timestamp,
            'amount': h.amount,
            'method': h.payment_method,
            'reason': h.reason,
            'service_name': f"{cs.service.name} for Plot {cs.land_description}",
            'payment_status': cs.payment_status,  # Based on total_paid() vs total_price()
        })

    return payment_data
