from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from django.utils import timezone
from apps.EasyDocs.analytics import get_revenue_from_payments
from apps.EasyDocs.models import Payment, ClientSubService, PaymentHistory
from django.db.models import F, Value, Case, When, DecimalField as DEC_FIELD
from django.db.models.functions import Coalesce
from django.db.models import Q  
import tempfile
from decimal import Decimal
from django.http import HttpResponse


def generate_revenue_pdf(filename, year, month=None):
    """
    Generate a clean PDF listing all payments and subservices for a given year or year+month
    using actual computed company and institution revenue splits.
    """
    # Determine up_to_date for filtering
    up_to_date = None
    if month:
        # Use last day of the month
        import calendar
        day = calendar.monthrange(year, month)[1]
        up_to_date = timezone.datetime(year, month, day, 23, 59, 59, tzinfo=timezone.get_current_timezone())

    # Fetch totals using existing function
    totals = get_revenue_from_payments(year, up_to_date=up_to_date)

    # Filter payments excluding subservices
    subservice_payment_ids = PaymentHistory.objects.filter(
        reason='sub_service', payment__payment_date__year=year
    ).values_list('payment_id', flat=True)

    payments_qs = Payment.objects.filter(payment_date__year=year).exclude(id__in=subservice_payment_ids)
    if month:
        payments_qs = payments_qs.filter(payment_date__month=month)

    # Annotate company/institution revenue like in analytics
    payments_qs = payments_qs.annotate(
        inst_cost_src=Coalesce('institution_cost_snapshot', 'client_service__service__total_price', Value(0), output_field=DEC_FIELD()),
        overridden_total_src=Coalesce('overridden_total_snapshot', 'client_service__full_total_price', Value(0), output_field=DEC_FIELD())
    )

    # Proportional company revenue computation
    safe_divisor = Case(
        When(overridden_total_src__gt=Value(0), then=F('overridden_total_src')),
        default=Value(1),
        output_field=DEC_FIELD()
    )
    company_rev_case = Case(
        When(
            Q(client_service__service__category=F('client_service__service__category')) & Q(overridden_total_src__gt=Value(0)),
            then=F('amount') - (F('amount') * F('inst_cost_src') / safe_divisor)
        ),
        default=F('amount'),
        output_field=DEC_FIELD()
    )
    annotated_payments = payments_qs.annotate(
        company_revenue=company_rev_case,
        institution_share=F('amount') - company_rev_case
    )

    # Filter subservices
    subservices_qs = ClientSubService.objects.filter(client_service__requested_at__year=year)
    if month:
        subservices_qs = subservices_qs.filter(added_on__month=month)

    # Annotate subservices revenue
    subservices_qs = subservices_qs.annotate(
        base_price=Coalesce(F('sub_service__price'), Value(0), output_field=DEC_FIELD()),
        effective_price=Coalesce(F('overridden_price'), F('sub_service__price'), Value(0), output_field=DEC_FIELD()),
        company_revenue=Case(
            When(effective_price__gt=Value(0), then=F('paid_amount') - (F('paid_amount') * F('base_price') / F('effective_price'))),
            default=Value(0),
            output_field=DEC_FIELD()
        ),
        institution_share=F('paid_amount') - Case(
            When(effective_price__gt=Value(0), then=F('paid_amount') - (F('paid_amount') * F('base_price') / F('effective_price'))),
            default=Value(0),
            output_field=DEC_FIELD()
        )
    )

    # PDF setup
    doc = SimpleDocTemplate(filename, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()

    # Title
    title_text = f"Revenue Report for {year}" + (f" Month: {month}" if month else "")
    elements.append(Paragraph(title_text, styles['Title']))
    elements.append(Spacer(1, 12))

    # Totals Table
    total_data = [
        ['Gross Revenue (KES)', 'Company Revenue (KES)', 'Institution Revenue (KES)'],
        [f"{totals['gross_total']:.2f}", f"{totals['company_total']:.2f}", f"{totals['inst_total']:.2f}"]
    ]
    total_table = Table(total_data, hAlign='LEFT')
    total_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
        ('TEXTCOLOR',(0,0),(-1,0),colors.black),
        ('ALIGN',(0,0),(-1,-1),'CENTER'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold')
    ]))
    elements.append(total_table)
    elements.append(Spacer(1, 24))

    # Payments Table
    elements.append(Paragraph("Main Payments (Excluding Subservices):", styles['Heading2']))
    payments_data = [['Client Service', 'Payment Date', 'Amount', 'Company Revenue', 'Institution Share']]
    for p in annotated_payments:
        payments_data.append([
            str(p.client_service),
            p.payment_date.strftime("%d-%b-%Y %H:%M"),
            f"{p.amount:.2f}",
            f"{p.company_revenue:.2f}",
            f"{p.institution_share:.2f}"
        ])
    if len(payments_data) == 1:
        payments_data.append(['No payments found', '', '', '', ''])

    payments_table = Table(payments_data, hAlign='LEFT', repeatRows=1)
    payments_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
        ('TEXTCOLOR',(0,0),(-1,0),colors.black),
        ('ALIGN',(2,1),(-1,-1),'RIGHT'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold')
    ]))
    elements.append(payments_table)
    elements.append(Spacer(1, 24))

    # Subservices Table
    elements.append(Paragraph("Subservice Payments:", styles['Heading2']))
    sub_data = [['Subservice', 'Added On', 'Paid Amount', 'Company Revenue', 'Institution Share']]
    for s in subservices_qs:
        sub_data.append([
            str(s.sub_service),
            s.added_on.strftime("%d-%b-%Y %H:%M"),
            f"{s.paid_amount:.2f}",
            f"{s.company_revenue:.2f}",
            f"{s.institution_share:.2f}"
        ])
    if len(sub_data) == 1:
        sub_data.append(['No subservices found', '', '', '', ''])

    sub_table = Table(sub_data, hAlign='LEFT', repeatRows=1)
    sub_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
        ('TEXTCOLOR',(0,0),(-1,0),colors.black),
        ('ALIGN',(2,1),(-1,-1),'RIGHT'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold')
    ]))
    elements.append(sub_table)

    # Build PDF
    doc.build(elements)
    return filename






def revenue_pdf_view(request):
    """Generate PDF for the current filters"""
    year = int(request.GET.get('year', timezone.now().year))
    month = request.GET.get('month')
    month = int(month) if month else None

    # Create temporary file for PDF
    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    pdf_path = generate_revenue_pdf(tmp_file.name, year, month=month)

    with open(pdf_path, 'rb') as f:
        pdf_data = f.read()

    response = HttpResponse(pdf_data, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="revenue_{year}{f"_{month}" if month else ""}.pdf"'
    return response