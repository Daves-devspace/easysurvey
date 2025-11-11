from calendar import month_abbr
from django.views.decorators.http import require_GET
from django.db.models import Sum, F, Q, Value, Case, When, DecimalField, ExpressionWrapper
from django.db.models.functions import Coalesce, TruncMonth, Greatest
from django.utils.timezone import now
from decimal import Decimal
from collections import OrderedDict, defaultdict
from datetime import date, datetime, time

from django.http import JsonResponse
from django.utils import timezone

from .models import Payment, Client, Service  # adjust to your model
from .models import Expense
from .models import ClientSubService, ServiceCategory, ClientService, PaymentHistory
from ..Employee.models import Payroll

# Reasonable precision for aggregates (adjust if needed)
DEC_FIELD = DecimalField(max_digits=18, decimal_places=2)
SMALL_DEC_FIELD = DecimalField(max_digits=18, decimal_places=6)  # for intermediate calc precision

from django.db.models import Q
import logging
logger = logging.getLogger(__name__)  
  
# def get_revenue_from_payments(year, up_to_date=None):
#     """
#     Unified revenue computation.

#     ✅ Respects snapshot values (institution_cost_snapshot & overridden_total_snapshot)
#     ✅ Filters payments safely up to 'up_to_date'
#     ✅ Separates ClientService vs SubService revenues
#     ✅ Quantizes to 2 decimals for accuracy
#     """
#     logger.info("▶️ Computing revenue for year=%s up_to_date=%s", year, up_to_date)

#     # --- Discover payments applied to subservices
#     subservice_payment_ids = list(
#         PaymentHistory.objects.filter(
#             reason='sub_service',
#             payment__payment_date__year=year
#         ).values_list('payment_id', flat=True)
#     )

#     # --- Prepare base queryset for ClientService-level payments
#     qs = (
#         Payment.objects
#         .filter(payment_date__year=year)
#         .exclude(id__in=subservice_payment_ids)
#         .select_related('client_service__service')
#     )

#     # --- Time filter
#     up_to_dt = None
#     if up_to_date:
#         tz = timezone.get_current_timezone()
#         date_part = up_to_date.date() if isinstance(up_to_date, datetime) else up_to_date
#         up_to_dt = timezone.make_aware(datetime.combine(date_part, time(23, 59, 59)), timezone=tz)
#         qs = qs.filter(payment_date__lte=up_to_dt)
#     logger.info("Filtered main payments count=%d", qs.count())

#     # --- Annotate payment fields safely
#     qs = qs.annotate(
#         inst_cost_src=Coalesce('institution_cost_snapshot',
#                                'client_service__service__total_price',
#                                Value(Decimal('0.00')), output_field=DEC_FIELD),
#         overridden_total_src=Coalesce('overridden_total_snapshot',
#                                       'client_service__full_total_price',
#                                       Value(Decimal('0.00')), output_field=DEC_FIELD)
#     )

#     safe_divisor = Case(
#         When(overridden_total_src__gt=0, then=F('overridden_total_src')),
#         default=Value(Decimal('1.00')), output_field=DEC_FIELD
#     )

#     # --- Compute company share for main service
#     proportional_expr = ExpressionWrapper(
#         F('amount') - (F('amount') * F('inst_cost_src') / safe_divisor),
#         output_field=SMALL_DEC_FIELD
#     )

#     company_rev = Case(
#         When(
#             Q(client_service__service__category=ServiceCategory.TITLE)
#             & Q(overridden_total_src__gt=0),
#             then=proportional_expr
#         ),
#         When(client_service__service__category=ServiceCategory.TITLE,
#              then=ExpressionWrapper(F('amount') - F('inst_cost_src'), output_field=SMALL_DEC_FIELD)),
#         default=F('amount'),
#         output_field=SMALL_DEC_FIELD
#     )

#     annotated = qs.annotate(
#         gross=F('amount'),
#         company_revenue=Greatest(company_rev, Value(Decimal('0.00')), output_field=SMALL_DEC_FIELD),
#         institution_share=ExpressionWrapper(F('amount') - F('company_revenue'), output_field=SMALL_DEC_FIELD)
#     )

#     aggs_services = annotated.aggregate(
#         gross_total=Coalesce(Sum('gross'), Value(Decimal('0.00')), output_field=DEC_FIELD),
#         company_total=Coalesce(Sum('company_revenue'), Value(Decimal('0.00')), output_field=DEC_FIELD),
#         inst_total=Coalesce(Sum('institution_share'), Value(Decimal('0.00')), output_field=DEC_FIELD),
#     )

#     # --- Subservice side
#     sub_qs = ClientSubService.objects.filter(client_service__requested_at__year=year)
#     if up_to_dt:
#         sub_qs = sub_qs.filter(added_on__lte=up_to_dt)

#     sub_qs = sub_qs.annotate(
#         base_price=Coalesce(F('sub_service__price'), Value(Decimal('0.00'))),
#         effective_price=Coalesce(F('overridden_price'), F('sub_service__price')),
#         gross=F('paid_amount'),
#         institution_cost=Case(
#             When(effective_price__gt=0,
#                  then=ExpressionWrapper(F('paid_amount') * F('base_price') / F('effective_price'),
#                                         output_field=DEC_FIELD)),
#             default=Value(Decimal('0.00')),
#             output_field=DEC_FIELD
#         ),
#         company_revenue=Greatest(
#             ExpressionWrapper(
#                 F('paid_amount') - (F('paid_amount') * F('base_price') / F('effective_price')),
#                 output_field=DEC_FIELD
#             ),
#             Value(Decimal('0.00'))
#         )
#     )

#     aggs_sub = sub_qs.aggregate(
#         gross_total=Coalesce(Sum('gross'), Value(Decimal('0.00')), output_field=DEC_FIELD),
#         company_total=Coalesce(Sum('company_revenue'), Value(Decimal('0.00')), output_field=DEC_FIELD),
#         inst_total=Coalesce(Sum('institution_cost'), Value(Decimal('0.00')), output_field=DEC_FIELD),
#     )

#     # --- Final combined totals
#     gross_total = (aggs_services['gross_total'] + aggs_sub['gross_total']).quantize(Decimal('0.01'))
#     company_total = (aggs_services['company_total'] + aggs_sub['company_total']).quantize(Decimal('0.01'))
#     inst_total = (aggs_services['inst_total'] + aggs_sub['inst_total']).quantize(Decimal('0.01'))

#     logger.info("✅ Revenue done → Gross=%s, Company=%s, Institution=%s", gross_total, company_total, inst_total)

#     return {
#         'gross_total': gross_total,
#         'company_total': company_total,
#         'inst_total': inst_total,
#     }
 


def monthly_company_revenue(year):
    """
    Robust monthly (Jan→Dec) company revenue list (Decimal) for `year`.

    - Uses Payments for service revenue; for TITLE services splits institution cost proportionally.
    - Uses ClientSubService for subservice profit: (overridden_price or sub.price) - sub.price.
    - Guards against division-by-zero and negative per-payment revenue.
    - Returns Jan..Dec list of Decimal amounts (zero-filled).
    """
    # ========== SERVICE PAYMENTS ==========
    qs = (
        Payment.objects
               .filter(payment_date__year=year)
               .select_related('client_service__service')
    )

    # Annotate with snapshots or fallbacks
    qs = qs.annotate(
        inst_cost_src=Coalesce('institution_cost_snapshot', 'client_service__service__total_price', Value(Decimal('0.00')), output_field=DEC_FIELD),
        overridden_total_src=Coalesce('overridden_total_snapshot', 'client_service__full_total_price', Value(Decimal('0.00')), output_field=DEC_FIELD),
    )

    # safe_divisor to avoid division by zero
    safe_divisor = Case(
        When(overridden_total_src__gt=Value(Decimal('0.00')), then=F('overridden_total_src')),
        default=Value(Decimal('1.00'), output_field=DEC_FIELD),
        output_field=DEC_FIELD
    )

    proportional_expr = ExpressionWrapper(
        F('amount') - (F('amount') * F('inst_cost_src') / safe_divisor),
        output_field=SMALL_DEC_FIELD
    )

    fallback_expr = ExpressionWrapper(
        F('amount') - F('inst_cost_src'),
        output_field=SMALL_DEC_FIELD
    )

    company_rev_case = Case(
        When(
            Q(client_service__service__category=ServiceCategory.TITLE) &
            Q(overridden_total_src__gt=Value(Decimal('0.00'))),
            then=proportional_expr
        ),
        When(
            Q(client_service__service__category=ServiceCategory.TITLE),
            then=fallback_expr
        ),
        default=F('amount'),
        output_field=SMALL_DEC_FIELD
    )

    company_rev_nonneg = Greatest(company_rev_case, Value(Decimal('0.00')), output_field=SMALL_DEC_FIELD)

    # Aggregate monthly
    month_qs = (
        qs.annotate(month=TruncMonth('payment_date'))
          .annotate(company_revenue=company_rev_nonneg)
          .values('month')
          .annotate(month_total=Coalesce(Sum('company_revenue'), Value(Decimal('0.00')), output_field=DEC_FIELD))
          .order_by('month')
    )

    # init months Jan..Dec zero-filled
    months = {m: Decimal('0.00') for m in range(1, 13)}
    for row in month_qs:
        if not row.get('month'):
            continue
        months[row['month'].month] = Decimal(row['month_total'] or Decimal('0.00')).quantize(Decimal('0.01'))

    # ========== SUBSERVICES ==========
    sub_qs = ClientSubService.objects.filter(client_service__requested_at__year=year).select_related('sub_service')

    sub_qs = sub_qs.annotate(
        gross=Coalesce(F('overridden_price'), F('sub_service__price')),
        company_revenue=ExpressionWrapper(
            Coalesce(F('overridden_price'), F('sub_service__price')) - F('sub_service__price'),
            output_field=SMALL_DEC_FIELD
        )
    )

    sub_month_qs = (
        sub_qs.annotate(month=TruncMonth('added_on'))
              .values('month')
              .annotate(month_total=Coalesce(Sum('company_revenue'), Value(Decimal('0.00')), output_field=DEC_FIELD))
              .order_by('month')
    )

    for row in sub_month_qs:
        if not row.get('month'):
            continue
        m = row['month'].month
        months[m] = (months[m] + Decimal(row['month_total'] or Decimal('0.00'))).quantize(Decimal('0.01'))

    # return Jan..Dec list of Decimals
    return [months[i] for i in range(1, 13)]


def get_yearly_revenue_data(year=None):
    """
    Produce monthly (Jan→Dec) aggregates of company finances.

    Breakdown:
      - revenue:
          * Service payments (client services) net of institution share.
          * Subservice profit (overridden - base).
      - expenses:
          * General expenses.
          * Payroll payouts.
          * Institution payouts (subservices with is_paid_to_legal_office=True).
      - net_profit = revenue - expenses

    Rules:
      * For ClientService payments:
          - Use snapshot prices for proportional split between company & institution.
      * For SubServices:
          - company_profit = overridden_price - base_price
          - institution_cost (base_price) only counted in expenses when actually paid.
      * All numeric values stored as Decimals and rounded to 2dp.
    """
    if not year:
        year = date.today().year

    # -------------------------------------------
    # Initialize months (1–12) with Decimal zeros
    # -------------------------------------------
    months = OrderedDict(
        (m, {'revenue': Decimal('0.00'),
             'expenses': Decimal('0.00'),
             'net_profit': Decimal('0.00')})
        for m in range(1, 13)
    )

    # ================================================================
    # 1️⃣ SERVICE PAYMENTS → COMPANY REVENUE (snapshot-aware proportional split)
    # ================================================================
    payments_qs = (
        Payment.objects
        .filter(payment_date__year=year)
        .select_related('client_service__service')
    )

    # Safely pull cost and overridden totals from snapshots or fallbacks
    payments_qs = payments_qs.annotate(
        inst_cost_src=Coalesce(
            'institution_cost_snapshot',
            'client_service__service__total_price',
            Value(Decimal('0.00')),
            output_field=DEC_FIELD
        ),
        overridden_total_src=Coalesce(
            'overridden_total_snapshot',
            'client_service__full_total_price',
            Value(Decimal('0.00')),
            output_field=DEC_FIELD
        ),
    )

    # Avoid division by zero — use 1 as safe fallback
    safe_divisor = Case(
        When(overridden_total_src__gt=Value(Decimal('0.00')), then=F('overridden_total_src')),
        default=Value(Decimal('1.00'), output_field=DEC_FIELD),
        output_field=DEC_FIELD,
    )

    # Proportional formula: company = amount - (amount * institution_cost / overridden_total)
    proportional_expr = ExpressionWrapper(
        F('amount') - (F('amount') * F('inst_cost_src') / safe_divisor),
        output_field=SMALL_DEC_FIELD,
    )

    # Fallback formula when no overridden total — subtract directly
    fallback_expr = ExpressionWrapper(
        F('amount') - F('inst_cost_src'),
        output_field=SMALL_DEC_FIELD,
    )

    # Choose correct logic per service type
    payment_company_case = Case(
        # TITLE services → proportional logic
        When(
            Q(client_service__service__category=ServiceCategory.TITLE) &
            Q(overridden_total_src__gt=Value(Decimal('0.00'))),
            then=proportional_expr
        ),
        # TITLE fallback
        When(
            Q(client_service__service__category=ServiceCategory.TITLE),
            then=fallback_expr
        ),
        # Others → assume full company revenue
        default=F('amount'),
        output_field=SMALL_DEC_FIELD,
    )

    # Ensure non-negative
    payment_company_nonneg = Greatest(payment_company_case, Value(Decimal('0.00')), output_field=SMALL_DEC_FIELD)

    # Aggregate by month
    payments_monthly = (
        payments_qs
        .annotate(month_trunc=TruncMonth('payment_date'))
        .values('month_trunc')
        .annotate(
            total_revenue=Coalesce(Sum(payment_company_nonneg), Value(Decimal('0.00')), output_field=DEC_FIELD)
        )
        .order_by('month_trunc')
    )

    # Fill months with revenue
    for row in payments_monthly:
        if row.get('month_trunc'):
            m = row['month_trunc'].month
            months[m]['revenue'] += Decimal(row['total_revenue'] or 0)

    # ================================================================
    # 2️⃣ SUBSERVICES → COMPANY PROFIT (overridden - base)
    # ================================================================
    sub_qs = (
        ClientSubService.objects
        .filter(client_service__requested_at__year=year)
        .select_related('sub_service')
    )

    sub_monthly = (
        sub_qs
        .annotate(month_trunc=TruncMonth('added_on'))
        .annotate(
            gross=Coalesce(F('overridden_price'), F('sub_service__price')),
            institution_cost=F('sub_service__price'),
            company_profit=ExpressionWrapper(
                Coalesce(F('overridden_price'), F('sub_service__price')) - F('sub_service__price'),
                output_field=SMALL_DEC_FIELD,
            ),
        )
        .values('month_trunc')
        .annotate(
            total_profit=Coalesce(Sum('company_profit'), Value(Decimal('0.00')), output_field=DEC_FIELD)
        )
        .order_by('month_trunc')
    )

    for row in sub_monthly:
        if row.get('month_trunc'):
            m = row['month_trunc'].month
            months[m]['revenue'] += Decimal(row['total_profit'] or 0)

    # ================================================================
    # 3️⃣ EXPENSES → General + Payroll + Subservice Institution Payouts
    # ================================================================

    # --- a) General expenses ---
    gen_q = (
        Expense.objects
        .filter(date__year=year)
        .annotate(month_trunc=TruncMonth('date'))
        .values('month_trunc')
        .annotate(total=Coalesce(Sum('amount'), Value(Decimal('0.00')), output_field=DEC_FIELD))
    )
    for row in gen_q:
        if row.get('month_trunc'):
            m = row['month_trunc'].month
            months[m]['expenses'] += Decimal(row['total'] or 0)

    # --- b) Payroll (only paid payrolls) ---
    payroll_q = (
        Payroll.objects
        .filter(is_paid=True, month__year=year)
        .annotate(month_trunc=TruncMonth('month'))
        .values('month_trunc')
        .annotate(total=Coalesce(Sum('net_salary'), Value(Decimal('0.00')), output_field=DEC_FIELD))
    )
    for row in payroll_q:
        if row.get('month_trunc'):
            m = row['month_trunc'].month
            months[m]['expenses'] += Decimal(row['total'] or 0)

    # --- c) Subservice institution payouts (only when actually paid to legal) ---
    sub_payout_q = (
        ClientSubService.objects
        .filter(client_service__requested_at__year=year, is_paid_to_legal_office=True)
        .annotate(month_trunc=TruncMonth('paid_at'))
        .values('month_trunc')
        .annotate(total=Coalesce(Sum('sub_service__price'), Value(Decimal('0.00')), output_field=DEC_FIELD))
    )
    for row in sub_payout_q:
        if row.get('month_trunc'):
            m = row['month_trunc'].month
            months[m]['expenses'] += Decimal(row['total'] or 0)

    # ================================================================
    # 4️⃣ COMPUTE NET PROFIT PER MONTH
    # ================================================================
    for m in months:
        months[m]['net_profit'] = (months[m]['revenue'] - months[m]['expenses']).quantize(Decimal('0.01'))

    # ================================================================
    # 5️⃣ OUTPUT STRUCTURE FOR FRONTEND (CHARTS / ANALYTICS)
    # ================================================================
    labels = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    revenue_arr = [months[m]['revenue'].quantize(Decimal('0.01')) for m in months]
    expenses_arr = [months[m]['expenses'].quantize(Decimal('0.01')) for m in months]
    net_arr = [months[m]['net_profit'] for m in months]

    return {
        'labels': labels,
        'revenue': revenue_arr,
        'expenses': expenses_arr,
        'net_profit': net_arr,
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


@require_GET
def monthly_service_analysis(request):
    """
    Returns monthly aggregated *company revenue* per service (Jan→Dec) for a given year.
    Also includes sub-services as separate series (prefixed with 'sub: ').
    Query params: year, service_id, client_id

    Notes:
      - Uses snapshot-aware proportional splits for TITLE services.
      - Returns zero-filled months so charts render even with no data.
    """
    try:
        year = int(request.GET.get('year', timezone.now().year))
    except ValueError:
        return JsonResponse({'error': 'Invalid year'}, status=400)

    service_id = request.GET.get('service_id')
    client_id = request.GET.get('client_id')

    # -------------------------
    # Payments (Services)
    # -------------------------
    p_filters = Q(payment_date__year=year)
    if service_id:
        p_filters &= Q(client_service__service_id=service_id)
    if client_id:
        p_filters &= Q(client_service__client_id=client_id)

    payments_qs = Payment.objects.filter(p_filters).select_related('client_service__service')

    # annotate snapshots
    payments_qs = payments_qs.annotate(
        inst_cost_src=Coalesce('institution_cost_snapshot', 'client_service__service__total_price', Value(Decimal('0.00')), output_field=DEC_FIELD),
        overridden_total_src=Coalesce('overridden_total_snapshot', 'client_service__full_total_price', Value(Decimal('0.00')), output_field=DEC_FIELD),
    )

    safe_divisor = Case(
        When(overridden_total_src__gt=Value(Decimal('0.00')), then=F('overridden_total_src')),
        default=Value(Decimal('1.00'), output_field=DEC_FIELD),
        output_field=DEC_FIELD
    )

    proportional_expr = ExpressionWrapper(
        F('amount') - (F('amount') * F('inst_cost_src') / safe_divisor),
        output_field=SMALL_DEC_FIELD
    )

    fallback_expr = ExpressionWrapper(
        F('amount') - F('inst_cost_src'),
        output_field=SMALL_DEC_FIELD
    )

    payment_company_case = Case(
        When(
            Q(client_service__service__category=ServiceCategory.TITLE) &
            Q(overridden_total_src__gt=Value(Decimal('0.00'))),
            then=proportional_expr
        ),
        When(
            Q(client_service__service__category=ServiceCategory.TITLE),
            then=fallback_expr
        ),
        default=F('amount'),
        output_field=SMALL_DEC_FIELD
    )

    payment_company_nonneg = Greatest(payment_company_case, Value(Decimal('0.00')), output_field=SMALL_DEC_FIELD)

    svc_q = (
        payments_qs
        .annotate(month=TruncMonth('payment_date'))
        .annotate(company_revenue=payment_company_nonneg)
        .values('client_service__service__name', 'month')
        .annotate(total=Coalesce(Sum('company_revenue'), Value(Decimal('0.00')), output_field=DEC_FIELD))
        .order_by('client_service__service__name', 'month')
    )

    # Convert to dictionary: {service_name: [12 months]}
    service_map = {}
    for row in svc_q:
        name = row.get('client_service__service__name') or 'Unknown Service'
        month_idx = (row['month'].month - 1) if row.get('month') else None
        if name not in service_map:
            service_map[name] = [Decimal('0.00')] * 12
        if month_idx is not None:
            service_map[name][month_idx] = Decimal(row['total'] or Decimal('0.00')).quantize(Decimal('0.01'))

    # -------------------------
    # SubServices: treat each sub_service.name as its own series (optional)
    # -------------------------
    sub_filters = Q(client_service__requested_at__year=year)
    if client_id:
        sub_filters &= Q(client_service__client_id=client_id)
    if service_id:
        sub_filters &= Q(client_service__service_id=service_id)

    sub_qs = ClientSubService.objects.filter(sub_filters).select_related('sub_service')

    # company_revenue for subservices (overridden_price - sub_service.price)
    sub_annot = (
        sub_qs
        .annotate(month=TruncMonth('added_on'))
        .annotate(
            gross=Coalesce(F('overridden_price'), F('sub_service__price')),
            company_revenue=ExpressionWrapper(Coalesce(F('overridden_price'), F('sub_service__price')) - F('sub_service__price'),
                                             output_field=SMALL_DEC_FIELD)
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
    # Build response (convert series data to floats for chart libs)
    # -------------------------
    labels = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    series = []
    total_revenue = Decimal('0.00')
    for name, arr in service_map.items():
        # many chart libraries accept floats; convert while preserving logic
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
