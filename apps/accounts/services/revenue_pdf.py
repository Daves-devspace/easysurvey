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
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from django.http import HttpResponse
from django.utils import timezone
from apps.EasyDocs.accounts.revenue import get_revenue_from_payments
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





def generate_revenue_excel(start_date, end_date):
    """Generate a clean, well-formatted Excel revenue report."""
    revenue_data = get_revenue_from_payments(
        start_date=start_date,
        end_date=end_date,
        profit_mode="auto"
    )

    wb = Workbook()
    ws = wb.active
    ws.title = "Revenue Report"

    # --- Styles ---
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4F81BD", fill_type="solid")
    total_fill = PatternFill(start_color="D9E1F2", fill_type="solid")
    border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )

    # --- Title ---
    ws.merge_cells('A1:G1')
    ws['A1'] = f"Revenue Report: {start_date.strftime('%d %b %Y')} to {end_date.strftime('%d %b %Y')}"
    ws['A1'].font = Font(bold=True, size=14)
    ws['A1'].alignment = Alignment(horizontal='center')

    ws.append([])

    # --- Totals Section ---
    ws.append(["Category", "Gross Revenue (KES)", "Company Revenue (KES)", "Institution Revenue (KES)"])
    for cell in ws[ws.max_row]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center')
        cell.border = border

    totals = [
        ["Main Services",
         revenue_data['main_services']['gross_total'],
         revenue_data['main_services']['company_total'],
         revenue_data['main_services']['inst_total']],
        ["Sub Services",
         revenue_data['subservices']['gross_total'],
         revenue_data['subservices']['company_total'],
         revenue_data['subservices']['inst_total']],
        ["TOTAL",
         revenue_data['gross_total'],
         revenue_data['company_total'],
         revenue_data['inst_total']]
    ]

    for row in totals:
        ws.append(row)
        for cell in ws[ws.max_row]:
            cell.border = border
            if row[0] == "TOTAL":
                cell.font = Font(bold=True)
                cell.fill = total_fill

    ws.append([])

    # --- Detailed Records ---
    ws.append(["#", "Client", "Service", "Client Charge", "Inst. Cost", "Profit", "Margin %"])
    for cell in ws[ws.max_row]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center')
        cell.border = border

    records = revenue_data.get('revenue_qs', [])
    if not records:
        ws.append(["No records found", "", "", "", "", "", ""])
    else:
        for idx, record in enumerate(records, 1):
            row = [
                idx,
                f"{record.client.first_name} {record.client.last_name}",
                record.service.name,
                float(record.client_charge),
                float(record.inst_cost),
                float(record.profit_amount),
                f"{record.profit_percent:.1f}%"
            ]
            ws.append(row)
            for cell in ws[ws.max_row]:
                cell.border = border
                if isinstance(cell.value, (int, float)):
                    cell.alignment = Alignment(horizontal='right')

    # --- Safe Auto Width Adjustment ---
    for i, col_cells in enumerate(ws.columns, 1):
        column_letter = get_column_letter(i)
        max_length = 0
        for cell in col_cells:
            try:
                if cell.value:
                    max_length = max(max_length, len(str(cell.value)))
            except Exception:
                pass
        ws.column_dimensions[column_letter].width = max_length + 2

    # --- Save to Temporary File ---
    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
    wb.save(tmp_file.name)
    return tmp_file.name








def revenue_excel_view(request):
    """Serve the Excel file as a download."""
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')

    try:
        start_date = timezone.datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = timezone.datetime.strptime(end_date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        start_date = timezone.now().date().replace(month=1, day=1)
        end_date = timezone.now().date()

    file_path = generate_revenue_excel(start_date, end_date)
    with open(file_path, 'rb') as f:
        file_data = f.read()

    response = HttpResponse(
        file_data,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="revenue_{start_date}_{end_date}.xlsx"'
    return response
