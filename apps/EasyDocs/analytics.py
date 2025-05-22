from calendar import month_abbr
from django.views.decorators.http import require_GET
from django.db.models import Sum, F
from django.db.models.functions import Coalesce, TruncMonth
from decimal import Decimal
from collections import OrderedDict, defaultdict
from datetime import date

from django.http import JsonResponse
from django.utils import timezone

from .models import Payment, Client, Service  # adjust to your model
from .models import Expense
from.models import ClientSubService


def get_yearly_revenue_data(year=None):
    if not year:
        year = date.today().year

    # Get all months initialized to 0
    months = OrderedDict((month, {'revenue': 0, 'expenses': 0, 'net_profit': 0}) for month in range(1, 13))

    # Get all payments
    payments = Payment.objects.filter(payment_date__year=year) \
        .annotate(month=TruncMonth('payment_date')) \
        .values('month') \
        .annotate(total=Sum('amount'))

    for item in payments:
        month_num = item['month'].month
        months[month_num]['revenue'] = float(item['total'])

    # General expenses
    gen_expenses = Expense.objects.filter(date__year=year) \
        .annotate(month=TruncMonth('date')) \
        .values('month') \
        .annotate(total=Sum('amount'))

    for item in gen_expenses:
        month_num = item['month'].month
        months[month_num]['expenses'] += float(item['total'])

    # Sub-service expenses
    sub_expenses = ClientSubService.objects.filter(added_on__year=year) \
        .annotate(month=TruncMonth('added_on')) \
        .values('month') \
        .annotate(
            total=Sum(
                Coalesce('overridden_price', F('sub_service__price'))
            )
        )
    # ✅ Payroll expenses
    payrolls = Payroll.objects.filter(is_paid=True, month__year=year) \
        .annotate(month=TruncMonth('month')) \
        .values('month') \
        .annotate(total=Sum('net_salary'))

    for item in payrolls:
        month_num = item['month'].month
        months[month_num]['expenses'] += float(item['total'])

    for item in sub_expenses:
        month_num = item['month'].month
        months[month_num]['expenses'] += float(item['total'])

    # Compute Net Profit
    for m in months:
        months[m]['net_profit'] = months[m]['revenue'] - months[m]['expenses']

    return {
        'labels': ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'],
        'revenue': [months[m]['revenue'] for m in months],
        'net_profit': [months[m]['net_profit'] for m in months]
    }


def get_available_years():
    years = Payment.objects.dates('payment_date', 'year', order='DESC')
    return [y.year for y in years]

@require_GET
def available_services(request):
    services = Service.objects.order_by('name').values('id','name')
    return JsonResponse({'services': list(services)})

@require_GET
def available_clients(request):
    clients = Client.objects.all().order_by('first_name','last_name')
    data = [{'id': c.id, 'name': f"{c.first_name} {c.last_name}"} for c in clients]
    return JsonResponse({'clients': data})

#
# def get_monthly_service_data(year):
#     data = (
#         Payment.objects
#         .filter(payment_date__year=year)
#         .values('client_service__service__name', 'payment_date__month')
#         .annotate(total=Sum('amount'))
#         .order_by('payment_date__month')
#     )
#
#     result = {}
#     for entry in data:
#         service = entry['client_service__service__name']
#         month = entry['payment_date__month']
#         amount = float(entry['total'])
#
#         if service not in result:
#             result[service] = [0] * 12
#         result[service][month - 1] = amount
#
#     # Now format to ApexCharts structure
#     response_data = {
#         "labels": list(month_abbr)[1:],  # ['Jan', 'Feb', ..., 'Dec']
#         "series": [
#             {"name": service, "data": monthly_data}
#             for service, monthly_data in result.items()
#         ]
#     }
#
#     return response_data
# views.py (Django function-based view)
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.utils import timezone
from django.db.models import Sum, Q

# Assume Payment model is already imported

@require_GET
def monthly_service_analysis(request):
    """
    Returns monthly aggregated payment data per service for the given year.

    Query Parameters:
      - year (int): filter by payment year (defaults to current year)
      - service_id (int): optional, filter for a specific service
      - client_id (int): optional, filter for a specific client
    """
    # Parse filters
    try:
        year = int(request.GET.get('year', timezone.now().year))
    except ValueError:
        return JsonResponse({'error': 'Invalid year parameter'}, status=400)

    service_id = request.GET.get('service_id')
    client_id = request.GET.get('client_id')

    # Build filter Q object
    filters = Q(payment_date__year=year)
    if service_id:
        filters &= Q(client_service__service_id=service_id)
    if client_id:
        filters &= Q(client_service__client_id=client_id)

    # Query and aggregate
    data_qs = (
        Payment.objects
               .filter(filters)
               .values('client_service__service__name', 'payment_date__month')
               .annotate(total=Sum('amount'))
    )

    # Initialize result dictionary for 12 months
    result = {}
    for entry in data_qs:
        service_name = entry['client_service__service__name']
        month_idx = entry['payment_date__month'] - 1
        amount = float(entry['total'])
        if service_name not in result:
            result[service_name] = [0.0] * 12
        result[service_name][month_idx] = amount

    # Prepare JSON response structure
    labels = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    series = [{ 'name': name, 'data': data } for name, data in result.items()]

    total_revenue = sum(sum(vals) for vals in result.values())
    total_services = len(series)

    response_data = {
        'year': year,
        'currency': 'KES',
        'total_services': total_services,
        'total_revenue': total_revenue,
        'labels': labels,
        'series': series
    }

    return JsonResponse(response_data)

# urls.py
# from django.urls import path
# from .views import monthly_service_analysis
# urlpatterns = [
#     path('api/analysis/monthly-services/', monthly_service_analysis, name='monthly-service-analysis'),
# ]
