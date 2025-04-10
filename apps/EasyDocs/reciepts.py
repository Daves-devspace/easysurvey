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


from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
import io
from decimal import Decimal
from django.utils import timezone

def generate_service_receipt(client_service, printed_by_user):
    """
    Generate a POS-style receipt PDF for a ClientService, including processes
    and sub-services with both price and paid columns.
    """
    width, height = (80 * mm, 200 * mm)
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(width, height))

    y = height - 10 * mm
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(width/2, y, "YOUR BUSINESS NAME")
    y -= 6 * mm

    c.setFont("Helvetica", 10)
    c.drawCentredString(width/2, y, "SERVICE RECEIPT")
    y -= 8 * mm

    c.setFont("Helvetica", 8)
    for line in [
        f"Client: {client_service.client.first_name} {client_service.client.last_name}",
        f"Land: {client_service.land_description}",
        f"Service: {client_service.service.name}",
        f"Requested: {client_service.requested_at.strftime('%Y-%m-%d %H:%M')}",
        "-" * 32
    ]:
        c.drawString(5*mm, y, line)
        y -= 5*mm

    # Header for processes
    c.setFont("Helvetica-Bold", 8)
    c.drawString(5*mm, y, "Process")
    c.drawRightString(width - 25*mm, y, "Price")
    c.drawRightString(width - 5*mm, y, "Paid")
    y -= 5*mm
    c.line(5*mm, y+2, width-5*mm, y+2)
    y -= 2*mm

    # Processes
    c.setFont("Helvetica", 8)
    for csp in client_service.service_processes.all().order_by('process__step_order'):
        name = csp.process.name[:12]
        price = csp.process.cost.quantize(Decimal('0.01'))
        paid = csp.paid_amount.quantize(Decimal('0.01'))

        c.drawString(5*mm, y, name)
        c.drawRightString(width - 25*mm, y, f"{price}")
        c.drawRightString(width - 5*mm, y, f"{paid}")
        y -= 5*mm

    # Sub-service header
    if client_service.sub_services.exists():
        y -= 3*mm
        c.setFont("Helvetica-Bold", 8)
        c.drawString(5*mm, y, "Sub-Service")
        c.drawRightString(width - 25*mm, y, "Price")
        c.drawRightString(width - 5*mm, y, "Paid")
        y -= 5*mm
        c.line(5*mm, y+2, width-5*mm, y+2)
        y -= 2*mm

        c.setFont("Helvetica", 8)
        for css in client_service.sub_services.all():
            name = css.sub_service.name[:12]
            price = css.sub_service.price.quantize(Decimal('0.01'))
            paid = css.paid_amount.quantize(Decimal('0.01'))

            c.drawString(5*mm, y, name)
            c.drawRightString(width - 25*mm, y, f"{price}")
            c.drawRightString(width - 5*mm, y, f"{paid}")
            y -= 5*mm

    # Totals
    y -= 2*mm
    c.line(5*mm, y, width-5*mm, y)
    y -= 4*mm

    total_paid = client_service.total_paid()
    total_balance = client_service.total_balance()

    c.setFont("Helvetica-Bold", 8)
    c.drawString(5*mm, y, "Total Paid:")
    c.drawRightString(width-5*mm, y, f"{total_paid.quantize(Decimal('0.01'))}")
    y -= 5*mm
    c.drawString(5*mm, y, "Balance:")
    c.drawRightString(width-5*mm, y, f"{total_balance.quantize(Decimal('0.01'))}")
    y -= 8*mm

    # Footer
    c.setFont("Helvetica", 6)
    ts = timezone.now().strftime("%Y-%m-%d %H:%M")
    c.drawString(5*mm, y, f"Printed by: {printed_by_user.get_full_name()}")
    y -= 4*mm
    c.drawString(5*mm, y, f"At: {ts}")

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
    # Allow inline display in iframe
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response
