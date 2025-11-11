# apps/EasyDocs/pdf_utils.py
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from django.utils import timezone
from apps.EasyDocs.accounts.revenue import get_revenue_from_payments
from decimal import Decimal
from django.http import HttpResponse
import tempfile


def generate_revenue_pdf(filename, start_date, end_date):
    """
    Generate a clean PDF listing all revenue records for a given date range.
    """
    # Fetch revenue data
    revenue_data = get_revenue_from_payments(
        start_date=start_date,
        end_date=end_date,
        profit_mode="auto"
    )

    # PDF setup
    doc = SimpleDocTemplate(filename, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()

    # Title
    title_text = f"Revenue Report: {start_date.strftime('%d %b %Y')} to {end_date.strftime('%d %b %Y')}"
    elements.append(Paragraph(title_text, styles['Title']))
    elements.append(Spacer(1, 12))

    # Totals Table
    total_data = [
        ['Category', 'Gross Revenue (KES)', 'Company Revenue (KES)', 'Institution Revenue (KES)'],
        [
            'Main Services',
            f"{revenue_data['main_services']['gross_total']:.2f}",
            f"{revenue_data['main_services']['company_total']:.2f}",
            f"{revenue_data['main_services']['inst_total']:.2f}"
        ],
        [
            'Sub Services',
            f"{revenue_data['subservices']['gross_total']:.2f}",
            f"{revenue_data['subservices']['company_total']:.2f}",
            f"{revenue_data['subservices']['inst_total']:.2f}"
        ],
        [
            'TOTAL',
            f"{revenue_data['gross_total']:.2f}",
            f"{revenue_data['company_total']:.2f}",
            f"{revenue_data['inst_total']:.2f}"
        ]
    ]
    
    total_table = Table(total_data, hAlign='LEFT')
    total_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, -1), (-1, -1), colors.grey),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold')
    ]))
    elements.append(total_table)
    elements.append(Spacer(1, 24))

    # Detailed Records Table
    elements.append(Paragraph("Detailed Revenue Records:", styles['Heading2']))
    
    records = revenue_data.get('revenue_qs', [])
    records_data = [['#', 'Client', 'Service', 'Client Charge', 'Inst. Cost', 'Profit', 'Margin %']]
    
    for idx, record in enumerate(records, 1):
        records_data.append([
            str(idx),
            f"{record.client.first_name} {record.client.last_name}",
            record.service.name,
            f"{record.client_charge:.2f}",
            f"{record.inst_cost:.2f}",
            f"{record.profit_amount:.2f}",
            f"{record.profit_percent:.1f}%"
        ])
    
    if len(records_data) == 1:
        records_data.append(['No records found', '', '', '', '', '', ''])

    records_table = Table(records_data, hAlign='LEFT', repeatRows=1)
    records_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('ALIGN', (3, 1), (-1, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold')
    ]))
    elements.append(records_table)

    # Build PDF
    doc.build(elements)
    return filename


def revenue_pdf_view(request):
    """Generate PDF for the current filters"""
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')
    
    try:
        start_date = timezone.datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = timezone.datetime.strptime(end_date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        start_date = timezone.now().date().replace(month=1, day=1)
        end_date = timezone.now().date()

    # Create temporary file for PDF
    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    pdf_path = generate_revenue_pdf(tmp_file.name, start_date, end_date)

    with open(pdf_path, 'rb') as f:
        pdf_data = f.read()

    response = HttpResponse(pdf_data, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="revenue_{start_date}_{end_date}.pdf"'
    return response