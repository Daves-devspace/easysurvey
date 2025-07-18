# utils/receipts.py
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404

from apps.EasyDocs.models import ClientService, SiteSettings, ServiceCategory

from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
import io
from decimal import Decimal
from django.utils import timezone

import logging

logger = logging.getLogger(__name__)


from decimal import Decimal, ROUND_HALF_UP

def safe_price(val):
    """
    Ensure val is a Decimal quantized to 2 places.
    Fallback to 0.00 if None or otherwise falsy.
    """
    # If val is already Decimal('0.00'), we still want to keep it.
    raw = val if val is not None else 0
    return Decimal(raw).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def generate_service_receipt(client_service, printed_by_user: User):
    """
    Generate a POS-style receipt PDF for a ClientService,
    including processes or services with both price and paid columns.
    """
    width, height = (80 * mm, 200 * mm)
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(width, height))

    settings = SiteSettings.objects.first()
    y = height - 10 * mm

    # ─── Header ────────────────────────────────────────────────────────────────
    
    try:
        site_settings = SiteSettings.objects.first()
        company_name = site_settings.company_name if site_settings and site_settings.company_name else "SMARTSURVEYOR"
    except Exception:
        company_name = "SMARTSURVEYOR"

    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(width/2, y, company_name)

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
        c.drawString(5 * mm, y, line)
        y -= 5 * mm

    # ─── Service / Process Header ─────────────────────────────────────────────
    c.setFont("Helvetica-Bold", 8)
    label = "Process" if client_service.service.category == ServiceCategory.TITLE else "Service"
    c.drawString(5 * mm, y, label)
    c.drawRightString(width - 25 * mm, y, "Price")
    c.drawRightString(width - 5 * mm, y, "Paid")
    y -= 5 * mm
    c.line(5 * mm, y + 2, width - 5 * mm, y + 2)
    y -= 2 * mm

    # ─── Main Content ──────────────────────────────────────────────────────────
    c.setFont("Helvetica", 8)
    if client_service.service.category == ServiceCategory.TITLE:
        for csp in client_service.service_processes.all().order_by('process__step_order'):
            name = csp.process.name[:12]

            price = safe_price(csp.overridden_cost if csp.overridden_cost is not None else csp.process.cost)
            paid  = safe_price(csp.paid_amount)
            c.drawString(5 * mm, y, name)
            c.drawRightString(width - 25 * mm, y, f"{price}")
            c.drawRightString(width - 5 * mm, y, f"{paid}")
            y -= 5 * mm
    else:
        name  = client_service.service.name[:12]
        # determine raw price
        raw = (
            client_service.overridden_total_price
            if client_service.overridden_total_price is not None
            else client_service.service.total_price
        )
        # DEBUG log
        logger.debug(
            "Receipt Debug — CS ID %s: overridden_total_price=%r, service.total_price=%r",
            client_service.pk,
            client_service.overridden_total_price,
            client_service.service.total_price,
        )
        price = safe_price(raw)
        paid  = safe_price(client_service.total_paid)
        c.drawString(5 * mm, y, name)
        c.drawRightString(width - 25 * mm, y, f"{price}")
        c.drawRightString(width - 5 * mm, y, f"{paid}")
        y -= 5 * mm

    # ─── Sub-Services ──────────────────────────────────────────────────────────
    if client_service.sub_services.exists():
        y -= 3 * mm
        c.setFont("Helvetica-Bold", 8)
        c.drawString(5 * mm, y, "Sub-Service")
        c.drawRightString(width - 25 * mm, y, "Price")
        c.drawRightString(width - 5 * mm, y, "Paid")
        y -= 5 * mm
        c.line(5 * mm, y + 2, width - 5 * mm, y + 2)
        y -= 2 * mm

        c.setFont("Helvetica", 8)
        for css in client_service.sub_services.all():
            name = css.sub_service.name[:12]
            price = safe_price(css.overridden_price if css.overridden_price is not None else css.sub_service.price)
            paid  = safe_price(css.paid_amount)
            c.drawString(5 * mm, y, name)
            c.drawRightString(width - 25 * mm, y, f"{price}")
            c.drawRightString(width - 5 * mm, y, f"{paid}")
            y -= 5 * mm

    # ─── Totals ────────────────────────────────────────────────────────────────
    y -= 2 * mm
    c.line(5 * mm, y, width - 5 * mm, y)
    y -= 4 * mm

    total_paid    = safe_price(client_service.total_paid)
    total_balance = safe_price(client_service.total_balance)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(5 * mm, y, "Total Paid:")
    c.drawRightString(width - 5 * mm, y, f"{total_paid}")
    y -= 5 * mm
    c.drawString(5 * mm, y, "Balance:")
    c.drawRightString(width - 5 * mm, y, f"{total_balance}")
    y -= 8 * mm

    # ─── Footer ────────────────────────────────────────────────────────────────
    c.setFont("Helvetica", 6)
    ts = timezone.localtime().strftime("%Y-%m-%d %H:%M")
    c.drawString(5 * mm, y, f"Printed by: {printed_by_user.first_name}")
    y -= 4 * mm
    c.drawString(5 * mm, y, f"At: {ts}")
    y -= 6 * mm

    if settings and settings.tagline:
        c.setFont("Helvetica-Oblique", 7)
        c.drawCentredString(width / 2, y, settings.tagline)

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer















# # views.py
# from django.http import HttpResponse
# from .utils.receipts import generate_service_receipt
@login_required
def download_receipt(request, cs_id):
    cs = get_object_or_404(ClientService, id=cs_id)
    buf = generate_service_receipt(cs, request.user)

    response = HttpResponse(buf, content_type='application/pdf')
    filename = f"receipt_{cs.id}.pdf"
    # Allow inline display in iframe
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response
