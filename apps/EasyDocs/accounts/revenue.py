# apps/EasyDocs/revenue.py
from decimal import Decimal
import calendar
from datetime import datetime, date, time as _time
from typing import Optional

from django.db.models import (
    Value, F, Q, Sum, DecimalField, Case, When, ExpressionWrapper, Subquery,
    OuterRef,  QuerySet
)
from django.db.models import BooleanField
from django.db.models.functions import Greatest
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.EasyDocs.models import (
    Payment, PaymentAdjustment, PaymentHistory, ClientSubService, ClientService, ServiceCategory, Expense
)
from django.shortcuts import render
from django.views import View


DEC_FIELD = DecimalField(max_digits=14, decimal_places=2)

def _as_end_of_day(dt):
    tz = timezone.get_current_timezone()
    if isinstance(dt, datetime):
        d = dt.date()
    else:
        d = dt
    end = datetime.combine(d, _time(23, 59, 59))
    return timezone.make_aware(end, timezone=tz)

def get_revenue_from_payments(
    *,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    year: Optional[int] = None,
    up_to_date: Optional[date] = None,
    service_status: Optional[str] = None,
    profit_mode: str = "auto",   # "auto" or "all"
    serialize: bool = False,     # if True, return rows as list-of-dicts (safe for JSON)
    limit: Optional[int] = None,
    offset: Optional[int] = None,
) -> dict:
    """
    Single source-of-truth revenue utility.

    Returns dict:
    {
      'gross_total': Decimal,
      'company_total': Decimal,
      'inst_total': Decimal,
      'main_services_aggs': {...},
      'subservices_aggs': {...},
      'revenue_qs': QuerySet (annotated ClientService) OR None (if serialize=True),
      'revenue_rows': [dict,...] if serialize=True else None,
    }

    Behavior:
    - Accepts start_date/end_date OR (year + optional up_to_date) for backward compatibility.
    - Respects snapshot fields (institution_cost_snapshot & overridden_total_snapshot).
    - profit_mode "auto": completed+fully_paid => full service margin, else realized margin.
      "all": returns both realized and service margins in annotations.
    - Uses DB annotations & subqueries for efficiency.
    """
    # -- determine date range
    if start_date and end_date:
        s = start_date
        e = end_date
    elif year:
        s = date(year, 1, 1)
        if up_to_date:
            e = up_to_date
        else:
            e = date(year, 12, 31)
    elif up_to_date:
        # if only up_to_date provided, treat as year of that date
        s = date(up_to_date.year, 1, 1)
        e = up_to_date
    else:
        # default: full current year
        now = timezone.now()
        s = date(now.year, 1, 1)
        e = date(now.year, 12, 31)

    start_dt = timezone.make_aware(datetime.combine(s, _time.min), timezone=timezone.get_current_timezone())
    end_dt = _as_end_of_day(e)

    # ------------- MAIN SERVICE PAYMENTS (exclude subservice payments by PaymentHistory) -------------
    subservice_payment_ids = list(
        PaymentHistory.objects.filter(
            reason='sub_service'
        ).values_list('payment_id', flat=True).distinct()
    )

    main_pay_qs = (
        Payment.objects
        .filter(payment_date__gte=start_dt, payment_date__lte=end_dt)
        .exclude(id__in=subservice_payment_ids)
        .select_related('client_service__service')
    )

    if service_status:
        main_pay_qs = main_pay_qs.filter(client_service__status=service_status)

    # annotate chosen sources (use snapshot else fallback)
    main_pay_qs = main_pay_qs.annotate(
        inst_cost_src=Coalesce('institution_cost_snapshot',
                               'client_service__service__total_price',
                               Value(Decimal('0.00')), output_field=DEC_FIELD),
        overridden_total_src=Coalesce('overridden_total_snapshot',
                                      'client_service__full_total_price',
                                      Value(Decimal('0.00')), output_field=DEC_FIELD)
    )

    safe_div = Case(
        When(overridden_total_src__gt=Value(Decimal('0.00')), then=F('overridden_total_src')),
        default=Value(Decimal('1.00')),
        output_field=DEC_FIELD
    )

    proportional_expr = ExpressionWrapper(
        F('amount') - (F('amount') * F('inst_cost_src') / safe_div),
        output_field=DEC_FIELD
    )

    company_case = Case(
        When(
            Q(client_service__service__category=ServiceCategory.TITLE) &
            Q(overridden_total_src__gt=Value(Decimal('0.00'))),
            then=proportional_expr
        ),
        When(
            Q(client_service__service__category=ServiceCategory.TITLE),
            then=ExpressionWrapper(F('amount') - F('inst_cost_src'), output_field=DEC_FIELD)
        ),
        default=F('amount'),
        output_field=DEC_FIELD
    )

    main_annot = main_pay_qs.annotate(
        gross=F('amount'),
        company_revenue=Greatest(company_case, Value(Decimal('0.00')), output_field=DEC_FIELD),
        institution_share=ExpressionWrapper(F('amount') - F('company_revenue'), output_field=DEC_FIELD)
    )

    # raw payment-only aggregates (always positive — gross cash received from clients)
    raw_pay_aggs = main_annot.aggregate(
        gross_total=Coalesce(Sum('gross'), Value(Decimal('0.00')), output_field=DEC_FIELD),
        company_total=Coalesce(Sum('company_revenue'), Value(Decimal('0.00')), output_field=DEC_FIELD),
        inst_total=Coalesce(Sum('institution_share'), Value(Decimal('0.00')), output_field=DEC_FIELD),
    )

    main_adj_qs = (
        PaymentAdjustment.objects
        .filter(created_at__gte=start_dt, created_at__lte=end_dt)
        .exclude(original_payment_id__in=subservice_payment_ids)
        .select_related('original_payment__client_service__service')
        .annotate(
            inst_cost_src=Coalesce(
                'original_payment__institution_cost_snapshot',
                'original_payment__client_service__service__total_price',
                Value(Decimal('0.00')),
                output_field=DEC_FIELD,
            ),
            overridden_total_src=Coalesce(
                'original_payment__overridden_total_snapshot',
                'original_payment__client_service__full_total_price',
                Value(Decimal('0.00')),
                output_field=DEC_FIELD,
            )
        )
    )

    if service_status:
        main_adj_qs = main_adj_qs.filter(original_payment__client_service__status=service_status)

    adj_safe_div = Case(
        When(overridden_total_src__gt=Value(Decimal('0.00')), then=F('overridden_total_src')),
        default=Value(Decimal('1.00')),
        output_field=DEC_FIELD
    )

    adj_proportional_expr = ExpressionWrapper(
        F('amount') - (F('amount') * F('inst_cost_src') / adj_safe_div),
        output_field=DEC_FIELD
    )

    adj_company_positive = Case(
        When(
            Q(original_payment__client_service__service__category=ServiceCategory.TITLE) &
            Q(overridden_total_src__gt=Value(Decimal('0.00'))),
            then=Greatest(adj_proportional_expr, Value(Decimal('0.00')), output_field=DEC_FIELD)
        ),
        When(
            Q(original_payment__client_service__service__category=ServiceCategory.TITLE),
            then=Greatest(
                ExpressionWrapper(F('amount') - F('inst_cost_src'), output_field=DEC_FIELD),
                Value(Decimal('0.00')),
                output_field=DEC_FIELD
            )
        ),
        default=F('amount'),
        output_field=DEC_FIELD
    )

    adj_gross_expr = ExpressionWrapper(F('amount') * Value(Decimal('-1.00')), output_field=DEC_FIELD)
    adj_company_expr = ExpressionWrapper(adj_company_positive * Value(Decimal('-1.00')), output_field=DEC_FIELD)
    adj_inst_expr = ExpressionWrapper(adj_gross_expr - adj_company_expr, output_field=DEC_FIELD)

    main_adj_annot = main_adj_qs.annotate(
        gross=adj_gross_expr,
        company_revenue=adj_company_expr,
        institution_share=adj_inst_expr,
    )

    main_adj_aggs = main_adj_annot.aggregate(
        gross_total=Coalesce(Sum('gross'), Value(Decimal('0.00')), output_field=DEC_FIELD),
        company_total=Coalesce(Sum('company_revenue'), Value(Decimal('0.00')), output_field=DEC_FIELD),
        inst_total=Coalesce(Sum('institution_share'), Value(Decimal('0.00')), output_field=DEC_FIELD),
    )

    # gross_inflow = raw cash received (always positive)
    # adj_outflow  = total reversed/adjusted (always positive — the actual amount returned)
    gross_inflow = raw_pay_aggs['gross_total']
    adj_outflow  = abs(main_adj_aggs['gross_total'])   # adj gross is stored negative, flip to positive

    main_aggs = {
        'gross_total': (raw_pay_aggs['gross_total'] + main_adj_aggs['gross_total']).quantize(Decimal('0.01')),
        'company_total': (raw_pay_aggs['company_total'] + main_adj_aggs['company_total']).quantize(Decimal('0.01')),
        'inst_total': (raw_pay_aggs['inst_total'] + main_adj_aggs['inst_total']).quantize(Decimal('0.01')),
        'gross_inflow': gross_inflow.quantize(Decimal('0.01')),
        'adj_outflow': adj_outflow.quantize(Decimal('0.01')),
    }

    # ------------- SUBSERVICE SIDE (ledger-based, period accurate) -------------
    sub_hist_qs = PaymentHistory.objects.filter(
        reason='sub_service',
        sub_service__isnull=False,
        created_at__gte=start_dt,
        created_at__lte=end_dt,
    ).select_related('sub_service__sub_service', 'sub_service__client_service')

    if service_status:
        sub_hist_qs = sub_hist_qs.filter(client_service__status=service_status)

    sub_hist_qs = sub_hist_qs.annotate(
        base_price=Coalesce(
            F('sub_service__institution_cost_snapshot'),
            F('sub_service__sub_service__price'),
            Value(Decimal('0.00')),
            output_field=DEC_FIELD,
        ),
        effective_price=Coalesce(
            F('sub_service__overridden_price_snapshot'),
            F('sub_service__overridden_price'),
            F('sub_service__sub_service__price'),
            Value(Decimal('0.00')),
            output_field=DEC_FIELD,
        ),
        gross=F('amount'),
        institution_cost=Case(
            When(
                effective_price__gt=Value(Decimal('0.00')),
                then=ExpressionWrapper(
                    F('amount') * F('base_price') / F('effective_price'),
                    output_field=DEC_FIELD,
                ),
            ),
            default=Value(Decimal('0.00')),
            output_field=DEC_FIELD,
        ),
        company_revenue=Case(
            When(
                effective_price__gt=Value(Decimal('0.00')),
                then=ExpressionWrapper(
                    F('amount') - (F('amount') * F('base_price') / F('effective_price')),
                    output_field=DEC_FIELD,
                ),
            ),
            default=F('amount'),
            output_field=DEC_FIELD,
        ),
    )

    sub_aggs = sub_hist_qs.aggregate(
        gross_total=Coalesce(Sum('gross'), Value(Decimal('0.00')), output_field=DEC_FIELD),
        company_total=Coalesce(Sum('company_revenue'), Value(Decimal('0.00')), output_field=DEC_FIELD),
        inst_total=Coalesce(Sum('institution_cost'), Value(Decimal('0.00')), output_field=DEC_FIELD),
        inflow_total=Coalesce(
            Sum(
                Case(
                    When(amount__gt=Value(Decimal('0.00')), then=F('amount')),
                    default=Value(Decimal('0.00')),
                    output_field=DEC_FIELD,
                )
            ),
            Value(Decimal('0.00')),
            output_field=DEC_FIELD,
        ),
        outflow_total=Coalesce(
            Sum(
                Case(
                    When(
                        amount__lt=Value(Decimal('0.00')),
                        then=ExpressionWrapper(F('amount') * Value(Decimal('-1.00')), output_field=DEC_FIELD),
                    ),
                    default=Value(Decimal('0.00')),
                    output_field=DEC_FIELD,
                )
            ),
            Value(Decimal('0.00')),
            output_field=DEC_FIELD,
        ),
    )

    # ------------- DETAILED PER-SERVICE (annotated ClientService QS) -------------
    # first payment snapshots subqueries
    first_pay = Payment.objects.filter(client_service=OuterRef("pk")).order_by("payment_date", "id")
    first_inst_subq = Subquery(first_pay.values("institution_cost_snapshot")[:1], output_field=DEC_FIELD)
    first_total_subq = Subquery(first_pay.values("overridden_total_snapshot")[:1], output_field=DEC_FIELD)

    history_in_period = PaymentHistory.objects.filter(
        client_service=OuterRef("pk"),
        created_at__gte=start_dt,
        created_at__lte=end_dt,
    ).values("client_service").annotate(
        s=Coalesce(Sum("amount"), Value(Decimal("0.00")))
    ).values("s")

    history_all = PaymentHistory.objects.filter(
        client_service=OuterRef("pk")
    ).values("client_service").annotate(
        s=Coalesce(Sum("amount"), Value(Decimal("0.00")))
    ).values("s")

    base_q = ClientService.objects.select_related("client", "service").filter(
        Q(requested_at__gte=start_dt, requested_at__lte=end_dt) |
        Q(payments__payment_date__gte=start_dt, payments__payment_date__lte=end_dt) |
        Q(payments__adjustments__created_at__gte=start_dt, payments__adjustments__created_at__lte=end_dt)
    ).distinct()
    if service_status:
        base_q = base_q.filter(status=service_status)

    inst_cost_expr = Coalesce(first_inst_subq, F("service__total_price"), Value(Decimal("0.00")), output_field=DEC_FIELD)
    client_charge_expr = Coalesce(first_total_subq, F("overridden_total_price"), F("full_total_price"), F("service__total_price"),
                                  Value(Decimal("0.00")), output_field=DEC_FIELD)
    total_paid_period_expr = Coalesce(
        Subquery(history_in_period, output_field=DEC_FIELD),
        Value(Decimal("0.00")),
        output_field=DEC_FIELD,
    )
    total_paid_all_expr = Coalesce(
        Subquery(history_all, output_field=DEC_FIELD),
        Value(Decimal("0.00")),
        output_field=DEC_FIELD,
    )

    service_margin_expr = ExpressionWrapper(client_charge_expr - inst_cost_expr, output_field=DEC_FIELD)
    realized_margin_expr = ExpressionWrapper(
        total_paid_period_expr * (service_margin_expr) / Greatest(client_charge_expr, Value(Decimal("0.01"))),
        output_field=DEC_FIELD
    )

    cs_qs = base_q.annotate(
        inst_cost=inst_cost_expr,
        client_charge=client_charge_expr,
        total_paid_in_period=total_paid_period_expr,
        total_paid_all=total_paid_all_expr,
        service_margin=service_margin_expr,
        realized_margin=realized_margin_expr,
    )

    # fully paid detection
    cs_qs = cs_qs.annotate(
        fully_paid=Case(
            When(total_paid_all__gte=F("client_charge"), then=Value(True)),
            default=Value(False),
            output_field=BooleanField()
        )
    )

    # compute profit_amount according to profit_mode
    if profit_mode == "all":
        # leave both service_margin and realized_margin exposed
        cs_qs = cs_qs.order_by("-requested_at")
    else:
        profit_display = Case(
            When(Q(status="completed") & Q(fully_paid=True), then=F("service_margin")),
            default=F("realized_margin"),
            output_field=DEC_FIELD
        )
        cs_qs = cs_qs.annotate(profit_amount=profit_display)
        cs_qs = cs_qs.annotate(
            profit_percent=ExpressionWrapper(
                Greatest(F("profit_amount"), Value(Decimal("0.00"))) * Value(Decimal("100.00")) /
                Greatest(F("client_charge"), Value(Decimal("0.01"))),
                output_field=DEC_FIELD
            )
        ).order_by("-requested_at")

    # pagination (optional)
    revenue_qs = cs_qs
    if offset:
        revenue_qs = revenue_qs[offset:]
    if limit:
        revenue_qs = revenue_qs[:limit]

    # final combined totals
    gross_total = (main_aggs['gross_total'] + sub_aggs['gross_total']).quantize(Decimal('0.01'))
    company_total = (main_aggs['company_total'] + sub_aggs['company_total']).quantize(Decimal('0.01'))
    inst_total = (main_aggs['inst_total'] + sub_aggs['inst_total']).quantize(Decimal('0.01'))

    total_gross_inflow = (
        main_aggs.get('gross_inflow', Decimal('0.00')) + sub_aggs.get('inflow_total', Decimal('0.00'))
    ).quantize(Decimal('0.01'))
    total_adj_outflow = (
        main_aggs.get('adj_outflow', Decimal('0.00')) + sub_aggs.get('outflow_total', Decimal('0.00'))
    ).quantize(Decimal('0.01'))

    result = {
        "gross_total": gross_total,
        "company_total": company_total,
        "inst_total": inst_total,
        # separated cash-flow metrics so UI can show gross inflows without going negative
        "gross_inflow": total_gross_inflow,
        "adj_outflow": total_adj_outflow,
        "main_services": main_aggs,
        "subservices": sub_aggs,
        "start_date": start_dt,
        "end_date": end_dt,
        "profit_mode": profit_mode,
        "revenue_qs": None if serialize else revenue_qs,
        "revenue_rows": None,
    }

    if serialize:
        rows = []
        # pick the fields you want in the JSON; don't return models
        for cs in revenue_qs:
            rows.append({
                "client_id": cs.client_id,
                "client_name": f"{cs.client.first_name} {cs.client.last_name}",
                "client_service_id": cs.id,
                "service_name": cs.service.name,
                "inst_cost": float(cs.inst_cost or Decimal("0.00")),
                "client_charge": float(cs.client_charge or Decimal("0.00")),
                "total_paid_in_period": float(cs.total_paid_in_period or Decimal("0.00")),
                "total_paid_all": float(cs.total_paid_all or Decimal("0.00")),
                "service_margin": float(cs.service_margin or Decimal("0.00")),
                "realized_margin": float(cs.realized_margin or Decimal("0.00")),
                "profit_amount": float(getattr(cs, "profit_amount", cs.realized_margin or Decimal("0.00")) or Decimal("0.00")),
                "profit_percent": float(getattr(cs, "profit_percent", Decimal("0.00")) or Decimal("0.00")),
                "status": cs.status,
                "fully_paid": bool(cs.fully_paid),
            })
        result["revenue_rows"] = rows

    return result







def parse_revenue_filters(request):
    """
    Parse revenue filter parameters from request.
    
    Returns:
        dict: {
            'start_date': date,
            'end_date': date,
            'revenue_year': int,
            'revenue_month': int or None,
            'filter_type': str ('month', 'range', or 'year')
        }
    """
    current_year = timezone.now().year
    current_month = timezone.now().month
    
    # Get filter parameters
    req_start_date = request.GET.get('start_date')
    req_end_date = request.GET.get('end_date')
    req_year = request.GET.get('rev_year')
    req_month = request.GET.get('rev_month')
    
    # Initialize defaults
    filter_type = 'year'
    revenue_year = current_year
    revenue_month = None
    
    # Determine filter type and calculate dates
    if req_start_date and req_end_date:
        # DATE RANGE FILTER
        filter_type = 'range'
        try:
            start_date = datetime.strptime(req_start_date, "%Y-%m-%d").date()
            end_date = datetime.strptime(req_end_date, "%Y-%m-%d").date()
            revenue_year = start_date.year
        except (ValueError, TypeError):
            # Fallback to current year
            start_date = datetime(current_year, 1, 1).date()
            end_date = datetime(current_year, 12, 31).date()
            
    elif req_year and req_month:
        # MONTH FILTER
        filter_type = 'month'
        try:
            revenue_year = int(req_year)
            revenue_month = int(req_month)
            
            # Calculate first and last day of the month
            start_date = datetime(revenue_year, revenue_month, 1).date()
            last_day = calendar.monthrange(revenue_year, revenue_month)[1]
            end_date = datetime(revenue_year, revenue_month, last_day).date()
        except (ValueError, TypeError):
            # Fallback to current month
            revenue_year = current_year
            revenue_month = current_month
            start_date = datetime(current_year, current_month, 1).date()
            last_day = calendar.monthrange(current_year, current_month)[1]
            end_date = datetime(current_year, current_month, last_day).date()
            
    elif req_year:
        # YEAR FILTER
        filter_type = 'year'
        try:
            revenue_year = int(req_year)
            start_date = datetime(revenue_year, 1, 1).date()
            end_date = datetime(revenue_year, 12, 31).date()
        except (ValueError, TypeError):
            # Fallback to current year
            revenue_year = current_year
            start_date = datetime(current_year, 1, 1).date()
            end_date = datetime(current_year, 12, 31).date()
    else:
        # DEFAULT: Current year
        start_date = datetime(current_year, 1, 1).date()
        end_date = datetime(current_year, 12, 31).date()
    
    return {
        'start_date': start_date,
        'end_date': end_date,
        'revenue_year': revenue_year,
        'revenue_month': revenue_month,
        'filter_type': filter_type,
        'current_year': current_year,
        'current_month': current_month,
    }


def get_revenue_context(request):
    """
    Get complete revenue context for templates.
    
    Args:
        request: Django request object
        
    Returns:
        dict: Complete context for revenue templates including:
            - Filter parameters
            - Revenue totals
            - Stat cards
            - Revenue records
    """
    # Parse filters
    filters = parse_revenue_filters(request)

    # Parse status filter (default = completed)
    status_filter = request.GET.get('status', 'all').lower()
    service_status = 'completed' if status_filter == 'completed' else None
    
    # Get revenue data
    revenue_data = get_revenue_from_payments(
        start_date=filters['start_date'],
        end_date=filters['end_date'],
        service_status=service_status,
        profit_mode="auto"
    )

    # Get detailed records
    revenue_records = revenue_data.get('revenue_qs', None)

    # ✅ Filter by completion status
    if revenue_records is not None:
        if status_filter == "completed":
            revenue_records = revenue_records.filter(status="completed")
        elif status_filter == "all":
            pass  # no filter
        else:
            # optional: handle unknown status value
            revenue_records = revenue_records.filter(status="completed")

    expenses_total = Expense.objects.filter(
        date__gte=filters['start_date'],
        date__lte=filters['end_date'],
    ).aggregate(
        total=Coalesce(Sum('amount'), Value(Decimal('0.00')), output_field=DEC_FIELD)
    )['total'] or Decimal('0.00')

    # Prepare totals
    revenue_totals = {
        'main_services': {
            'gross_total': revenue_data.get('main_services', {}).get('gross_total', Decimal('0.00')),
            'company_total': revenue_data.get('main_services', {}).get('company_total', Decimal('0.00')),
            'inst_total': revenue_data.get('main_services', {}).get('inst_total', Decimal('0.00')),
        },
        'subservices': {
            'gross_total': revenue_data.get('subservices', {}).get('gross_total', Decimal('0.00')),
            'company_total': revenue_data.get('subservices', {}).get('company_total', Decimal('0.00')),
            'inst_total': revenue_data.get('subservices', {}).get('inst_total', Decimal('0.00')),
        },
        'gross_total': revenue_data.get('gross_total', Decimal('0.00')),
        'company_total': revenue_data.get('company_total', Decimal('0.00')),
        'inst_total': revenue_data.get('inst_total', Decimal('0.00')),
    }

    stat_cards = {
        # gross cash actually received — always positive, never goes negative due to reversals
        'client_payments': revenue_data.get('gross_inflow', Decimal('0.00')),
        # total reversed/adjusted in the period — always positive
        'adj_outflow': revenue_data.get('adj_outflow', Decimal('0.00')),
        'inst_payment': revenue_data.get('inst_total', Decimal('0.00')),
        'expenses': expenses_total,
        # net company revenue (can reflect prior-period reversal impact)
        'revenue': revenue_data.get('company_total', Decimal('0.00')),
    }

    return {
        'current_year': filters['current_year'],
        'current_month': filters['current_month'],
        'revenue_year': filters['revenue_year'],
        'revenue_month': filters['revenue_month'],
        'start_date': filters['start_date'],
        'end_date': filters['end_date'],
        'filter_type': filters['filter_type'],
        'revenue_totals': revenue_totals,
        'stat_cards': stat_cards,
        'revenue_records': revenue_records,
        'status_filter': status_filter,  # 👈 include for frontend toggle
    }