from calendar import month_abbr
from django.views.decorators.http import require_GET
from django.db.models import Sum, F, Q
from django.db.models.functions import Coalesce, TruncMonth, Greatest
from decimal import Decimal
from collections import OrderedDict, defaultdict
from datetime import date, datetime

from django.http import JsonResponse
from django.utils import timezone

from .models import Payment, Client, Service  # adjust to your model
from .models import Expense
from.models import ClientSubService, ServiceCategory, ClientService
from ..Employee.models import Payroll
from django.db.models import Case, When
from django.db.models import DecimalField, ExpressionWrapper
from django.db.models import Sum, F, DecimalField, ExpressionWrapper, Value

# Company revenue expression:
# For TITLE services: overridden_total_price - institution cost (service.total_price)
# For others: overridden_total_price (or payment amount)
#Dashbord/stats
# Single function to compute YTD/company revenue
# Reasonable precision for aggregates (adjust if  need different)
DEC_FIELD = DecimalField(max_digits=18, decimal_places=2)


def get_revenue_from_payments(year, up_to_date=None):
    """
    Returns a 3-tuple of totals for a given year (optionally up to a date):
      - gross_revenue: total client payments collected
      - company_revenue: net retained revenue (profit)
      - institution_share_paid: amounts due/paid out to institutions (external parties)
    
    ⚡ Includes both:
      - Payments from Services (ClientService)
      - Virtualized payments from SubServices (ClientSubService)
    """
    # =============================
    # 1. Filter main payments (Services)
    # =============================
    qs = Payment.objects.filter(payment_date__year=year).select_related('client_service__service')

    if up_to_date:
        # end-of-day cutoff
        tz = timezone.get_current_timezone()
        up_to_dt = datetime(year, up_to_date.month, up_to_date.day, 23, 59, 59, tzinfo=tz)
        qs = qs.filter(payment_date__lte=up_to_dt)

    # proportional share for TITLE services (split between institution vs company)
    proportional_inst_share = ExpressionWrapper(
        F('client_service__service__total_price') * (F('amount') / F('client_service__full_total_price')),
        output_field=DecimalField(max_digits=18, decimal_places=6)
    )

    # Calculate company revenue on each payment row
    company_rev_case = Case(
        # TITLE with known full_total_price → proportional split
        When(
            Q(client_service__service__category=ServiceCategory.TITLE) &
            Q(client_service__full_total_price__gt=Value(0)),
            then=ExpressionWrapper(
                F('amount') - proportional_inst_share,
                output_field=DecimalField(max_digits=18, decimal_places=6)
            )
        ),
        # TITLE but no full_total_price → fallback: subtract institution cost
        When(
            Q(client_service__service__category=ServiceCategory.TITLE),
            then=ExpressionWrapper(
                F('amount') - F('client_service__service__total_price'),
                output_field=DecimalField(max_digits=18, decimal_places=6)
            )
        ),
        # Default (non-title services): company takes the whole amount
        default=F('amount'),
        output_field=DecimalField(max_digits=18, decimal_places=6)
    )

    company_rev_nonneg = Greatest(
        company_rev_case,
        Value(Decimal('0.00')),
        output_field=DecimalField()
    )

    annotated = qs.annotate(
        gross=F('amount'),
        company_revenue=company_rev_nonneg,
        institution_share=ExpressionWrapper(
            F('amount') - company_rev_nonneg,
            output_field=DecimalField(max_digits=18, decimal_places=6)
        )
    )

    aggs_services = annotated.aggregate(
        gross_total=Coalesce(Sum('gross'), Value(Decimal('0.00')), output_field=DEC_FIELD),
        company_total=Coalesce(Sum('company_revenue'), Value(Decimal('0.00')), output_field=DEC_FIELD),
        inst_total=Coalesce(Sum('institution_share'), Value(Decimal('0.00')), output_field=DEC_FIELD),
    )

    # =============================
    # 2. Virtual SubService Flows
    # =============================
    
    sub_qs = ClientSubService.objects.filter(client_service__requested_at__year=year)


    if up_to_date:
        sub_qs = sub_qs.filter(added_on__lte=up_to_date)

    sub_qs = sub_qs.annotate(
        gross=Coalesce(F('overridden_price'), F('sub_service__price')),
        institution_cost=F('sub_service__price'),
        company_revenue=ExpressionWrapper(
            Coalesce(F('overridden_price'), F('sub_service__price')) - F('sub_service__price'),
            output_field=DEC_FIELD
        )
    )

    aggs_sub = sub_qs.aggregate(
        gross_total=Coalesce(Sum('gross'), Value(Decimal('0.00')), output_field=DEC_FIELD),
        company_total=Coalesce(Sum('company_revenue'), Value(Decimal('0.00')), output_field=DEC_FIELD),
        inst_total=Coalesce(Sum('institution_cost'), Value(Decimal('0.00')), output_field=DEC_FIELD),
    )

    # =============================
    # 3. Combine Service + SubService
    # =============================
    return (
        (Decimal(aggs_services['gross_total']) + Decimal(aggs_sub['gross_total'])).quantize(Decimal('0.01')),
        (Decimal(aggs_services['company_total']) + Decimal(aggs_sub['company_total'])).quantize(Decimal('0.01')),
        (Decimal(aggs_services['inst_total']) + Decimal(aggs_sub['inst_total'])).quantize(Decimal('0.01')),
    )



def monthly_company_revenue(year):
    """
    Robust monthly (Jan→Dec) company revenue list (Decimal) for `year`.

    - Uses Payments for service revenue; for TITLE services splits institution cost proportionally.
    - Uses ClientSubService for subservice profit: (overridden_price or sub.price) - sub.price.
    - Guards against division-by-zero and negative per-payment revenue.
    """
    # ========== SERVICE PAYMENTS ==========
    qs = (
        Payment.objects
               .filter(payment_date__year=year)
               .select_related('client_service__service')
    )

    # Precompute fields:
    # overridden_total: the client charge to split across payments (overridden_total or full_total_price)
    # institution_cost: the service.total_price (amount to be paid to external institution)
    qs = qs.annotate(
        overridden_total=Coalesce(
            F('client_service__overridden_total_price'),
            F('client_service__full_total_price'),
            Value(Decimal('0.00'), output_field=DEC_FIELD)
        ),
        institution_cost=Coalesce(
            F('client_service__service__total_price'),
            Value(Decimal('0.00'), output_field=DEC_FIELD)
        )
    )

    # Branch 1: proportional split when overridden_total > 0
    proportional_expr = ExpressionWrapper(
        F('amount') - (F('amount') * F('institution_cost') / F('overridden_total')),
        output_field=DecimalField(max_digits=18, decimal_places=6)
    )

    # Branch 2: fallback when overridden_total == 0: amount - institution_cost (clamped later)
    fallback_expr = ExpressionWrapper(
        F('amount') - F('institution_cost'),
        output_field=DecimalField(max_digits=18, decimal_places=6)
    )

    # Full expression per payment:
    company_rev_case = Case(
        When(
            Q(client_service__service__category=ServiceCategory.TITLE) &
            Q(overridden_total__gt=Value(Decimal('0.00'))),
            then=proportional_expr
        ),
        When(
            Q(client_service__service__category=ServiceCategory.TITLE),
            then=fallback_expr
        ),
        default=F('amount'),
        output_field=DecimalField(max_digits=18, decimal_places=6)
    )

    # Avoid negative per-payment revenue
    company_rev_nonneg = Greatest(company_rev_case, Value(Decimal('0.00')), output_field=DecimalField())

    # Aggregate monthly
    month_qs = (
        qs.annotate(month=TruncMonth('payment_date'))
          .annotate(company_revenue=company_rev_nonneg)
          .values('month')
          .annotate(month_total=Coalesce(Sum('company_revenue'), Value(Decimal('0.00')), output_field=DEC_FIELD))
          .order_by('month')
    )

    months = {m: Decimal('0.00') for m in range(1, 13)}
    for row in month_qs:
        if row['month'] is None:
            continue
        months[row['month'].month] = Decimal(row['month_total'] or Decimal('0.00')).quantize(Decimal('0.01'))

    # ========== SUBSERVICES ==========
    # Subservices contribute company profit = (overridden_price or sub.price) - sub.price
    sub_qs = ClientSubService.objects.filter(client_service__requested_at__year=year).select_related('sub_service')

    sub_qs = sub_qs.annotate(
        gross=Coalesce(F('overridden_price'), F('sub_service__price')),
        company_revenue=ExpressionWrapper(
            Coalesce(F('overridden_price'), F('sub_service__price')) - F('sub_service__price'),
            output_field=DecimalField(max_digits=18, decimal_places=6)
        )
    )

    sub_month_qs = (
        sub_qs.annotate(month=TruncMonth('added_on'))
              .values('month')
              .annotate(month_total=Coalesce(Sum('company_revenue'), Value(Decimal('0.00')), output_field=DEC_FIELD))
              .order_by('month')
    )

    for row in sub_month_qs:
        if row['month'] is None:
            continue
        m = row['month'].month
        months[m] += Decimal(row['month_total'] or Decimal('0.00')).quantize(Decimal('0.01'))

    # return Jan..Dec list of Decimals
    return [months[i] for i in range(1, 13)]




DEC_FIELD = DecimalField(max_digits=18, decimal_places=2)

def get_yearly_revenue_data(year=None):
    """
    Produce monthly (Jan→Dec) aggregates:
      - revenue: company retained revenue (service payments net of institution share + subservice profit)
      - expenses: general expenses + payroll + subservice institution payouts (only when paid to legal)
      - net_profit: revenue - expenses

    Key rules:
      - For TITLE services: split institution cost proportionally across payments.
      - For SubServices:
          * company_profit = (overridden_price or sub_service.price) - sub_service.price
          * institution_cost (sub_service.price) is counted in expenses only when is_paid_to_legal_office=True
            and grouped by paid_at (end-of-day grouping).
      - All monetary values are handled as Decimal and quantized to 2dp at the end.
    """
    if not year:
        year = date.today().year

    # initialize months 1..12 using Decimal for money
    months = OrderedDict(
        (m, {'revenue': Decimal('0.00'), 'expenses': Decimal('0.00'), 'net_profit': Decimal('0.00')})
        for m in range(1, 13)
    )

    # -------------------------
    # 1) SERVICE PAYMENTS -> company revenue per payment
    # -------------------------
    payments_qs = Payment.objects.filter(payment_date__year=year).select_related('client_service__service')

    # Build CASE expression for company revenue per payment:
    # - TITLE with full_total_price > 0 -> proportional split
    # - TITLE with no full_total_price -> fallback subtract service.total_price
    # - Non-title -> company keeps the full payment
    # Greatest(...) protects from negative (e.g., small payment < inst share)
    payment_company_case = Case(
        When(
            Q(client_service__service__category=ServiceCategory.TITLE) &
            Q(client_service__full_total_price__gt=Value(0)),
            then=ExpressionWrapper(
                F('amount') - (
                    F('client_service__service__total_price') * (F('amount') / F('client_service__full_total_price'))
                ),
                output_field=DecimalField(max_digits=18, decimal_places=6)
            )
        ),
        When(
            Q(client_service__service__category=ServiceCategory.TITLE),
            then=ExpressionWrapper(
                F('amount') - F('client_service__service__total_price'),
                output_field=DecimalField(max_digits=18, decimal_places=6)
            )
        ),
        default=F('amount'),
        output_field=DecimalField(max_digits=18, decimal_places=6)
    )
    payment_company_nonneg = Greatest(payment_company_case, Value(Decimal('0.00')), output_field=DecimalField())

    # Annotate by month and sum company_revenue
    payments_monthly = (
        payments_qs
        .annotate(month_trunc=TruncMonth('payment_date'))
        .values('month_trunc')
        .annotate(total_revenue=Coalesce(Sum(payment_company_nonneg), Value(Decimal('0.00')), output_field=DEC_FIELD))
        .order_by('month_trunc')
    )

    for row in payments_monthly:
        if not row['month_trunc']:
            continue
        m = row['month_trunc'].month
        months[m]['revenue'] += Decimal(row['total_revenue'] or Decimal('0.00'))

    # -------------------------
    # 2) SUBSERVICE CONTRIBUTIONS -> company profit (not expense)
    # -------------------------
    # Use ClientSubService.added_on as the month when the client was charged the sub-service
    sub_qs = ClientSubService.objects.filter(client_service__requested_at__year=year).select_related('sub_service')

    sub_monthly = (
        sub_qs
        .annotate(month_trunc=TruncMonth('added_on'))
        .annotate(
            gross=Coalesce(F('overridden_price'), F('sub_service__price')),
            institution_cost=F('sub_service__price'),
            company_profit=ExpressionWrapper(
                Coalesce(F('overridden_price'), F('sub_service__price')) - F('sub_service__price'),
                output_field=DecimalField(max_digits=18, decimal_places=6)
            )
        )
        .values('month_trunc')
        .annotate(total_profit=Coalesce(Sum('company_profit'), Value(Decimal('0.00')), output_field=DEC_FIELD))
        .order_by('month_trunc')
    )

    for row in sub_monthly:
        if not row['month_trunc']:
            continue
        m = row['month_trunc'].month
        months[m]['revenue'] += Decimal(row['total_profit'] or Decimal('0.00'))

    # -------------------------
    # 3) EXPENSES: General + Payroll + SUBSERVICE PAYOUTS (only when paid to legal)
    # -------------------------
    # General expenses (by Expense.date)
    gen_q = (
        Expense.objects.filter(date__year=year)
        .annotate(month_trunc=TruncMonth('date'))
        .values('month_trunc')
        .annotate(total=Coalesce(Sum('amount'), Value(Decimal('0.00')), output_field=DEC_FIELD))
    )
    for row in gen_q:
        if not row['month_trunc']:
            continue
        m = row['month_trunc'].month
        months[m]['expenses'] += Decimal(row['total'] or Decimal('0.00'))

    # Payroll (by Payroll.month) - only include paid payrolls
    payroll_q = (
        Payroll.objects.filter(is_paid=True, month__year=year)
        .annotate(month_trunc=TruncMonth('month'))
        .values('month_trunc')
        .annotate(total=Coalesce(Sum('net_salary'), Value(Decimal('0.00')), output_field=DEC_FIELD))
    )
    for row in payroll_q:
        if not row['month_trunc']:
            continue
        m = row['month_trunc'].month
        months[m]['expenses'] += Decimal(row['total'] or Decimal('0.00'))

    # SubService institution payouts: count sub_service.price only when is_paid_to_legal_office=True
    # Grouped by paid_at (actual payment timestamp)
    sub_payout_q = (
        ClientSubService.objects
        .filter(client_service__requested_at__year=year, is_paid_to_legal_office=True)
        .annotate(month_trunc=TruncMonth('paid_at'))
        .values('month_trunc')
        .annotate(total=Coalesce(Sum('sub_service__price'), Value(Decimal('0.00')), output_field=DEC_FIELD))
    )
    for row in sub_payout_q:
        if not row['month_trunc']:
            continue
        m = row['month_trunc'].month
        months[m]['expenses'] += Decimal(row['total'] or Decimal('0.00'))

    # -------------------------
    # 4) NET profit per month (revenue - expenses)
    # -------------------------
    for m in months:
        months[m]['net_profit'] = (months[m]['revenue'] - months[m]['expenses']).quantize(Decimal('0.01'))

    # Prepare arrays (keep Decimal for backend; convert to floats if your chart lib requires floats)
    labels = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    revenue_arr = [months[m]['revenue'].quantize(Decimal('0.01')) for m in months]
    expenses_arr = [months[m]['expenses'].quantize(Decimal('0.01')) for m in months]
    net_arr = [months[m]['net_profit'] for m in months]

    return {
        'labels': labels,
        'revenue': revenue_arr,
        'expenses': expenses_arr,
        'net_profit': net_arr
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
    Returns monthly aggregated *company revenue* per service (Jan→Dec) for a given year.
    Also includes sub-services as separate series (prefixed with 'sub: ').
    Query params: year, service_id, client_id
    """
    try:
        year = int(request.GET.get('year', timezone.now().year))
    except ValueError:
        return JsonResponse({'error': 'Invalid year'}, status=400)

    service_id = request.GET.get('service_id')
    client_id = request.GET.get('client_id')

    # -------------------------
    # 1) SERVICE PAYMENTS -> company revenue per payment
    # -------------------------
    p_filters = Q(payment_date__year=year)
    if service_id:
        p_filters &= Q(client_service__service_id=service_id)
    if client_id:
        p_filters &= Q(client_service__client_id=client_id)

    payments_qs = Payment.objects.filter(p_filters).select_related('client_service__service')

    payment_company_case = Case(
        When(
            Q(client_service__service__category=ServiceCategory.TITLE) &
            Q(client_service__full_total_price__gt=Value(0)),
            then=ExpressionWrapper(
                F('amount') - (
                    F('client_service__service__total_price') * (F('amount') / F('client_service__full_total_price'))
                ),
                output_field=DecimalField(max_digits=18, decimal_places=6)
            )
        ),
        When(
            Q(client_service__service__category=ServiceCategory.TITLE),
            then=ExpressionWrapper(
                F('amount') - F('client_service__service__total_price'),
                output_field=DecimalField(max_digits=18, decimal_places=6)
            )
        ),
        default=F('amount'),
        output_field=DecimalField(max_digits=18, decimal_places=6)
    )
    payment_company_nonneg = Greatest(payment_company_case, Value(Decimal('0.00')), output_field=DecimalField())

    svc_q = (
        payments_qs
        .annotate(month=TruncMonth('payment_date'))
        .annotate(company_revenue=payment_company_nonneg)
        .values('client_service__service__name', 'month')
        .annotate(total=Coalesce(Sum('company_revenue'), Value(Decimal('0.00')), output_field=DEC_FIELD))
        .order_by('client_service__service__name', 'month')
    )

    # build service -> 12 months map
    service_map = {}
    for row in svc_q:
        name = row.get('client_service__service__name') or 'Unknown Service'
        month_idx = (row['month'].month - 1) if row.get('month') else None
        if name not in service_map:
            service_map[name] = [Decimal('0.00')] * 12
        if month_idx is not None:
            service_map[name][month_idx] = Decimal(row['total'] or Decimal('0.00')).quantize(Decimal('0.01'))

    # -------------------------
    # 2) SUBSERVICES -> treat each sub_service.name as its own series
    #    (company_revenue = overridden_price - sub_service.price)
    # -------------------------
    sub_filters = Q(client_service__requested_at__year=year)
    if client_id:
        sub_filters &= Q(client_service__client_id=client_id)
    if service_id:
        sub_filters &= Q(client_service__service_id=service_id)

    sub_qs = ClientSubService.objects.filter(sub_filters).select_related('sub_service')

    sub_annot = (
        sub_qs
        .annotate(month=TruncMonth('added_on'))
        .annotate(
            gross=Coalesce(F('overridden_price'), F('sub_service__price')),
            company_revenue=ExpressionWrapper(Coalesce(F('overridden_price'), F('sub_service__price')) - F('sub_service__price'),
                                             output_field=DecimalField(max_digits=18, decimal_places=6))
        )
        .values('sub_service__name', 'month')
        .annotate(total=Coalesce(Sum('company_revenue'), Value(Decimal('0.00')), output_field=DEC_FIELD))
        .order_by('sub_service__name', 'month')
    )

    for row in sub_annot:
        name = f"sub: {row.get('sub_service__name') or 'Unknown'}"
        month_idx = (row['month'].month - 1) if row.get('month') else None
        if name not in service_map:
            service_map[name] = [Decimal('0.00')] * 12
        if month_idx is not None:
            service_map[name][month_idx] = Decimal(row['total'] or Decimal('0.00')).quantize(Decimal('0.01'))

    # -------------------------
    # 3) Build response (convert series data to floats for chart libs)
    # -------------------------
    labels = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    series = []
    total_revenue = Decimal('0.00')
    for name, arr in service_map.items():
        # many chart libraries accept floats; keep the precision by converting
        series.append({'name': name, 'data': [float(v) for v in arr]})
        total_revenue += sum(arr)

    response_data = {
        'year': year,
        'currency': 'KES',
        'total_services': len(service_map),
        'total_revenue': float(total_revenue.quantize(Decimal('0.01'))),
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
