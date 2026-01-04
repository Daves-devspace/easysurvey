# apps/EasyDocs/pdf_utils.py
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
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


def generate_revenue_pdf(buffer, start_date, end_date):
    """
    Generate a clean, landscape PDF listing all revenue records.
    Optimized for data fitting and pagination.
    
    Args:
        buffer: File-like object (HttpResponse or BytesIO) to write the PDF to.
        start_date: datetime.date object
        end_date: datetime.date object
    """
    # 1. Fetch data
    revenue_data = get_revenue_from_payments(
        start_date=start_date,
        end_date=end_date,
        profit_mode="auto"
    )

    # 2. Setup Document (Landscape for wider tables)
    # A4 Landscape is approx 842 points wide x 595 points high
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=30,
        leftMargin=30,
        topMargin=30,
        bottomMargin=30
    )

    elements = []
    styles = getSampleStyleSheet()
    
    # Custom Styles
    style_title = styles['Title']
    style_normal = styles['Normal']
    
    # Style for table text (smaller font, wrapped)
    style_cell = ParagraphStyle(
        'CellText',
        parent=styles['Normal'],
        fontSize=9,
        leading=11,  # Line spacing
        alignment=TA_LEFT
    )
    
    # Style for numeric columns
    style_cell_right = ParagraphStyle(
        'CellRight',
        parent=style_cell,
        alignment=TA_RIGHT
    )

    # 3. Report Header
    title_text = f"Revenue Report: {start_date.strftime('%d %b %Y')} to {end_date.strftime('%d %b %Y')}"
    elements.append(Paragraph(title_text, style_title))
    elements.append(Spacer(1, 20))

    # 4. Summary Table (Totals)
    # Define explicit widths to center it nicely
    total_col_widths = [150, 120, 120, 120]
    
    total_data = [
        ['Category', 'Gross (KES)', 'Company (KES)', 'Institution (KES)'],
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
    
    t_summary = Table(total_data, colWidths=total_col_widths, hAlign='CENTER')
    t_summary.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'), # Align numbers right
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),   # Align labels left
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), # Header bold
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'), # Footer/Total bold
        ('BACKGROUND', (0, -1), (-1, -1), colors.whitesmoke),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(t_summary)
    elements.append(Spacer(1, 30))

    # 5. Detailed Records Header
    elements.append(Paragraph("Detailed Revenue Records", styles['Heading2']))
    elements.append(Spacer(1, 10))

    # 6. Detailed Table Setup
    # Total usable width ~ 780pts. Let's distribute:
    # # (30) | Client (160) | Service (190) | Charge (100) | Inst (100) | Profit (100) | Margin (60) = ~740pts
    col_widths = [30, 160, 190, 100, 100, 100, 60]
    
    headers = ['#', 'Client', 'Service', 'Client Charge', 'Inst. Cost', 'Profit', '%']
    
    # Prepare data rows
    # IMPORTANT: We use Paragraph() objects inside cells to allow text wrapping.
    table_data = [headers]
    
    records = revenue_data.get('revenue_qs', [])
    
    if not records:
        table_data.append(['-', 'No records found', '', '', '', '', ''])
    else:
        for idx, record in enumerate(records, 1):
            # Wrap long text fields
            client_p = Paragraph(f"{record.client.first_name} {record.client.last_name}", style_cell)
            service_p = Paragraph(record.service.name, style_cell)
            
            # Format numbers as strings
            charge = f"{record.client_charge:,.2f}"
            inst = f"{record.inst_cost:,.2f}"
            profit = f"{record.profit_amount:,.2f}"
            margin = f"{record.profit_percent:.1f}%"
            
            # Color profit if negative (optional visual aid)
            # if record.profit_amount < 0: profit = Paragraph(f"<font color='red'>{profit}</font>", style_cell_right)
            
            row = [
                str(idx), 
                client_p, 
                service_p, 
                charge, 
                inst, 
                profit, 
                margin
            ]
            table_data.append(row)

    # 7. Create Table object
    # repeatRows=1 ensures the header appears on every new page
    t_records = Table(table_data, colWidths=col_widths, repeatRows=1, hAlign='LEFT')
    
    t_records.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),     # Header background
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),       # Header font
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),                  # Header alignment
        
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),                   # Vertical align top for wrapped text
        ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),     # Grid lines
        
        ('ALIGN', (3, 1), (-1, -1), 'RIGHT'),                  # Align numeric columns right
        ('FONTSIZE', (0, 0), (-1, -1), 9),                     # Font size for all
        
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))

    elements.append(t_records)

    # 8. Build PDF
    doc.build(elements)
    return buffer

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
