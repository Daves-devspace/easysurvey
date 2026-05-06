# utils/receipts.py
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.conf import settings

from apps.EasyDocs.models import ClientService, SiteSettings, ServiceCategory

from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import io
from decimal import Decimal
from django.utils import timezone
import os

import logging

logger = logging.getLogger(__name__)

# Try to register better fonts with improved error handling
try:
    # Try multiple possible font paths
    font_paths = [
        os.path.join(settings.BASE_DIR, 'static', 'fonts', 'Roboto-Regular.ttf'),
        os.path.join(settings.BASE_DIR, 'staticfiles', 'fonts', 'Roboto-Regular.ttf'),
        os.path.join(settings.STATIC_ROOT, 'fonts', 'Roboto-Regular.ttf') if settings.STATIC_ROOT else None,
        '/usr/share/fonts/truetype/roboto/Roboto-Regular.ttf',  # Common system path
    ]
    
    font_path = None
    for path in font_paths:
        if path and os.path.exists(path):
            font_path = path
            break
    
    if font_path:
        pdfmetrics.registerFont(TTFont('Roboto', font_path))
        pdfmetrics.registerFont(TTFont('Roboto-Bold', font_path.replace('Regular', 'Bold')))
        pdfmetrics.registerFont(TTFont('Roboto-Light', font_path.replace('Regular', 'Light')))
        ROBOTO = "Roboto"
        ROBOTO_BOLD = "Roboto-Bold"
        ROBOTO_LIGHT = "Roboto-Light"
    else:
        raise FileNotFoundError("Roboto font files not found")
        
except Exception as e:
    logger.warning(f"Custom fonts not available: {e}. Using Helvetica as fallback")
    # Fallback to Helvetica if custom fonts aren't available
    ROBOTO = "Helvetica"
    ROBOTO_BOLD = "Helvetica-Bold"
    ROBOTO_LIGHT = "Helvetica"

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

    # Define colors
    primary_color = HexColor("#1976D2")  # A professional blue
    accent_color = HexColor("#42A5F5")   # A lighter blue
    dark_color = HexColor("#0D47A1")     # A dark blue for emphasis
    light_blue = HexColor("#E3F2FD")     # Very light blue for watermark
    
    # ─── Watermark Background ────────────────────────────────────────────────
    try:
        site_settings = SiteSettings.objects.first()
        company_name = site_settings.company_name if site_settings and site_settings.company_name else "Plotsync"
    except Exception:
        company_name = "Plotsync"
    
    # Create a semi-transparent watermark
    c.saveState()
    c.setFillColor(light_blue)
    c.setFont(ROBOTO_LIGHT, 32)
    c.rotate(45)
    # Make watermark more transparent
    c.setFillAlpha(0.1)
    c.drawString(40 * mm, -10 * mm, company_name)
    c.restoreState()

    # ─── Header ────────────────────────────────────────────────────────────────
    c.setFillColor(primary_color)
    c.setFont(ROBOTO_BOLD, 14)
    c.drawCentredString(width/2, y, company_name)

    y -= 6 * mm
    c.setFillColor(accent_color)
    c.setFont(ROBOTO_BOLD, 12)
    c.drawCentredString(width/2, y, "SERVICE RECEIPT")
    y -= 8 * mm

    c.setFillColor(HexColor("#000000"))  # Black for content
    c.setFont(ROBOTO, 9)
    for line in [
        f"Client: {client_service.client.first_name} {client_service.client.last_name}",
        f"Land: {client_service.land_description}",
        f"Service: {client_service.service.name}",
        f"Requested: {client_service.requested_at.strftime('%Y-%m-%d %H:%M')}",
    ]:
        c.drawString(5 * mm, y, line)
        y -= 5 * mm
        
    # Draw a blue separator line
    y -= 2 * mm
    c.setStrokeColor(primary_color)
    c.setLineWidth(0.5)
    c.line(5 * mm, y, width - 5 * mm, y)
    y -= 4 * mm

    # ─── Service / Process Header ─────────────────────────────────────────────
    c.setFillColor(dark_color)
    c.setFont(ROBOTO_BOLD, 9)
    label = "Process" if client_service.service.category == ServiceCategory.TITLE else "Service"
    c.drawString(5 * mm, y, label)
    c.drawRightString(width - 25 * mm, y, "Price")
    c.drawRightString(width - 5 * mm, y, "Paid")
    y -= 4 * mm
    
    # Draw a thin blue line under the header
    c.setStrokeColor(accent_color)
    c.setLineWidth(0.3)
    c.line(5 * mm, y + 1, width - 5 * mm, y + 1)
    y -= 3 * mm

    # ─── Main Content ──────────────────────────────────────────────────────────
    c.setFillColor(HexColor("#000000"))  # Black for content
    c.setFont(ROBOTO, 8)
    if client_service.service.category == ServiceCategory.TITLE:
        for csp in client_service.service_processes.all().order_by('process__step_order'):
            name = csp.process.name[:20]  # Increased character limit for better readability

            price = safe_price(csp.overridden_cost if csp.overridden_cost is not None else csp.process.cost)
            paid  = safe_price(csp.paid_amount)
            c.drawString(5 * mm, y, name)
            c.drawRightString(width - 25 * mm, y, f"{price}")
            c.drawRightString(width - 5 * mm, y, f"{paid}")
            y -= 5 * mm
    else:
        name  = client_service.service.name[:20]  # Increased character limit
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
        c.setFillColor(dark_color)
        c.setFont(ROBOTO_BOLD, 9)
        c.drawString(5 * mm, y, "Sub-Service")
        c.drawRightString(width - 25 * mm, y, "Price")
        c.drawRightString(width - 5 * mm, y, "Paid")
        y -= 4 * mm
        
        # Draw a thin blue line under the sub-service header
        c.setStrokeColor(accent_color)
        c.setLineWidth(0.3)
        c.line(5 * mm, y + 1, width - 5 * mm, y + 1)
        y -= 3 * mm

        c.setFillColor(HexColor("#000000"))  # Black for content
        c.setFont(ROBOTO, 8)
        for css in client_service.sub_services.all():
            name = css.sub_service.name[:20]  # Increased character limit
            price = safe_price(css.overridden_price if css.overridden_price is not None else css.sub_service.price)
            paid  = safe_price(css.paid_amount)
            c.drawString(5 * mm, y, name)
            c.drawRightString(width - 25 * mm, y, f"{price}")
            c.drawRightString(width - 5 * mm, y, f"{paid}")
            y -= 5 * mm

    # ─── Totals ────────────────────────────────────────────────────────────────
    y -= 2 * mm
    c.setStrokeColor(primary_color)
    c.setLineWidth(0.5)
    c.line(5 * mm, y, width - 5 * mm, y)
    y -= 4 * mm

    total_paid    = safe_price(client_service.total_paid)
    total_balance = safe_price(client_service.total_balance)
    
    c.setFillColor(dark_color)
    c.setFont(ROBOTO_BOLD, 9)
    c.drawString(5 * mm, y, "Total Paid:")
    c.drawRightString(width - 5 * mm, y, f"{total_paid}")
    y -= 5 * mm
    c.drawString(5 * mm, y, "Balance:")
    c.drawRightString(width - 5 * mm, y, f"{total_balance}")
    y -= 8 * mm

    # ─── Footer ────────────────────────────────────────────────────────────────
    c.setFillColor(HexColor("#000000"))  # Black for footer
    c.setFont(ROBOTO, 7)
    ts = timezone.localtime().strftime("%Y-%m-%d %H:%M")
    c.drawString(5 * mm, y, f"Printed by: {printed_by_user.first_name}")
    y -= 4 * mm
    c.drawString(5 * mm, y, f"At: {ts}")
    y -= 6 * mm

    if settings and settings.tagline:
        c.setFillColor(accent_color)
        c.setFont(ROBOTO_LIGHT, 7)
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
