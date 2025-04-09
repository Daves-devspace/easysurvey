# utils/receipts.py

import io
from decimal import Decimal

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import landscape, portrait

from apps.EasyDocs.models import ClientService


def generate_service_receipt(client_service, printed_by_user):
    """
    Generate a POS-style receipt PDF for a ClientService.

    :param client_service: instance of ClientService
    :param printed_by_user: User instance who printed/sent the receipt
    :return: BytesIO buffer containing the PDF
    """
    # Receipt dimensions: 80mm wide, auto height up to ~200mm
    width, height = (80 * mm, 200 * mm)
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(width, height))

    # Starting y position
    y = height - 10 * mm

    # 1) Header: Business name (centered)
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(width / 2, y, "YOUR BUSINESS NAME")
    y -= 6 * mm

    # 2) Subheader: Receipt title
    c.setFont("Helvetica", 10)
    c.drawCentredString(width / 2, y, "SERVICE RECEIPT")
    y -= 8 * mm

    # 3) Client & Service Info
    c.setFont("Helvetica", 8)
    lines = [
        f"Client: {client_service.client.first_name} {client_service.client.last_name}",
        f"Land: {client_service.land_description}",
        f"Service: {client_service.service.name}",
        f"Requested: {client_service.requested_at.strftime('%Y-%m-%d %H:%M')}",
        "-" * 32
    ]
    for line in lines:
        c.drawString(5 * mm, y, line)
        y -= 5 * mm

    # 4) Table header
    c.setFont("Helvetica-Bold", 8)
    c.drawString(5 * mm, y, "Process")
    c.drawRightString(width - 5 * mm, y, "Amt")
    y -= 5 * mm
    c.line(5 * mm, y + 2, width - 5 * mm, y + 2)
    y -= 2 * mm

    # 5) Line items: each ClientServiceProcess
    c.setFont("Helvetica", 8)
    for csp in client_service.service_processes.all().order_by('process__step_order'):
        name = csp.process.name[:15]  # truncate if long
        paid = csp.paid_amount.quantize(Decimal('0.01'))
        line = f"{name}"
        c.drawString(5 * mm, y, line)
        c.drawRightString(width - 5 * mm, y, f"{paid}")
        y -= 5 * mm

    # 6) Totals
    y -= 2 * mm
    c.line(5 * mm, y, width - 5 * mm, y)
    y -= 4 * mm
    total_paid = client_service.total_paid()
    total_balance = client_service.total_balance()
    c.setFont("Helvetica-Bold", 8)
    c.drawString(5 * mm, y, "Total Paid:")
    c.drawRightString(width - 5 * mm, y, f"{total_paid.quantize(Decimal('0.01'))}")
    y -= 5 * mm
    c.drawString(5 * mm, y, "Balance:")
    c.drawRightString(width - 5 * mm, y, f"{total_balance.quantize(Decimal('0.01'))}")
    y -= 8 * mm

    # 7) Footer: printed by & timestamp
    c.setFont("Helvetica", 6)
    ts = timezone.now().strftime("%Y-%m-%d %H:%M")
    c.drawString(5 * mm, y, f"Printed by: {printed_by_user.get_full_name()}")
    y -= 4 * mm
    c.drawString(5 * mm, y, f"At: {ts}")
    y -= 4 * mm

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer


# # views.py
# from django.http import HttpResponse
# from .utils.receipts import generate_service_receipt

def download_receipt(request, cs_id):
    cs = get_object_or_404(ClientService, id=cs_id)
    buf = generate_service_receipt(cs, request.user)

    response = HttpResponse(buf, content_type='application/pdf')
    filename = f"receipt_{cs.id}.pdf"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
