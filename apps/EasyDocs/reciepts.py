# utils/receipts.py
import io
import os
import logging
from decimal import Decimal, ROUND_HALF_UP
from django.utils import timezone
from django.conf import settings as django_settings
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404

from apps.EasyDocs.models import ClientService, SiteSettings, ServiceCategory

from reportlab.lib.units import mm
from reportlab.lib.pagesizes import A5
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader

logger = logging.getLogger(__name__)

# Register Fonts
try:
    font_paths = [
        os.path.join(django_settings.BASE_DIR, 'static', 'fonts', 'Roboto-Regular.ttf'),
        os.path.join(django_settings.BASE_DIR, 'staticfiles', 'fonts', 'Roboto-Regular.ttf'),
        os.path.join(django_settings.STATIC_ROOT, 'fonts', 'Roboto-Regular.ttf') if django_settings.STATIC_ROOT else None,
        '/usr/share/fonts/truetype/roboto/Roboto-Regular.ttf', 
    ]
    font_path = next((p for p in font_paths if p and os.path.exists(p)), None)

    if font_path:
        pdfmetrics.registerFont(TTFont('Roboto', font_path))
        pdfmetrics.registerFont(TTFont('Roboto-Bold', font_path.replace('Regular', 'Bold')))
        pdfmetrics.registerFont(TTFont('Roboto-Light', font_path.replace('Regular', 'Light')))
        ROBOTO = "Roboto"
        ROBOTO_BOLD = "Roboto-Bold"
        ROBOTO_LIGHT = "Roboto-Light"
    else:
        raise FileNotFoundError("Roboto fonts not found")

except Exception as e:
    logger.warning(f"Custom fonts not available: {e}. Using Helvetica as fallback")
    ROBOTO = "Helvetica"
    ROBOTO_BOLD = "Helvetica-Bold"
    ROBOTO_LIGHT = "Helvetica"

def safe_price(val):
    raw = val if val is not None else 0
    return Decimal(raw).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

def draw_image_safe(c, image_field, x, y, width, height, preserve_aspect=True):
    if image_field and image_field.name:
        try:
            path = image_field.path
            if os.path.exists(path):
                img = ImageReader(path)
                c.drawImage(img, x, y, width, height, mask='auto', preserveAspectRatio=preserve_aspect)
                return True
        except Exception as e:
            logger.warning(f"Failed to load image {image_field}: {e}")
    return False

def generate_service_receipt(client_service, printed_by_user: User):
    """
    Generate an A5 geometric, fully branded receipt PDF.
    """
    width, height = A5
    margin = 12 * mm
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A5)

    settings = SiteSettings.objects.first()
    
    config = {
        "bank_name": "KCB BANK",
        "account_name": settings.company_name.upper() if settings else "GEOSPOT SURVEYS",
        "account_no": "7812000000",
        "paybill": "123456",
        "mpesa_phone": settings.company_phone if settings and settings.company_phone else "0792 944 218",
        "address_1": "29M7+M8G Nyahururu, Koinange Road & Kikuyu Kenya",
        "address_2": "Kikuyu Town & Othaya Building, First Floor, RM 25, Nyahururu Town",
        "services_list": "Land Surveyors | Geospatial Engineers | Valuation | Planners | GIS | Real Estate",
        "website": "www.geospotsurveys.co.ke"
    }
    
    brand_blue = HexColor("#0A194F")
    brand_gold = HexColor("#C19A2B")
    brand_red = HexColor("#B30000")
    brand_green = HexColor("#1E882D")
    text_dark = HexColor("#222222")
    bg_gray = HexColor("#F0F0F0")

    c.saveState()

    # ─── 1. BACKGROUND GEOMETRIC SHAPES ───
    c.setFillColor(brand_gold)
    c.rect(0, height - 6 * mm, width, 6 * mm, fill=1, stroke=0)
    c.rect(width - 6 * mm, height - 48 * mm, 6 * mm, 48 * mm, fill=1, stroke=0)

    c.setFillColor(brand_blue)
    p = c.beginPath()
    p.moveTo(0, height)
    p.lineTo(75 * mm, height)
    p.lineTo(75 * mm, height - 50 * mm)
    p.lineTo(0, height - 75 * mm)
    p.close()
    c.drawPath(p, fill=1, stroke=0)

    base_y = height - 50 * mm
    h = 16 * mm
    p = c.beginPath()
    p.moveTo(width - 6 * mm, base_y - (h * 0.15))
    p.lineTo(width, base_y - (h * 0.35))
    p.lineTo(width, base_y - h)
    p.lineTo(width - 6 * mm, base_y - (h * 0.80))
    p.close()
    c.drawPath(p, fill=1, stroke=0)

    c.setFillColor(brand_gold)
    p = c.beginPath()
    p.moveTo(0, 47 * mm)
    p.lineTo(25 * mm, 12 * mm)
    p.lineTo(0, 12 * mm)
    p.close()
    c.drawPath(p, fill=1, stroke=0)

    c.restoreState()

    # ─── 2. HEADER CONTENT ───
    logo_drawn = False
    if settings and settings.logo:
        logo_drawn = draw_image_safe(c, settings.logo, 10 * mm, height - 40 * mm, 50 * mm, 30 * mm, preserve_aspect=True)
    
    if not logo_drawn:
        c.setFillColor(HexColor("#FFFFFF"))
        c.setFont(ROBOTO_BOLD, 14)
        c.drawCentredString(37.5 * mm, height - 30 * mm, settings.company_name if settings else "GeoSPOT SURVEYS")
        if settings and settings.tagline:
            c.setFont(ROBOTO, 8)
            c.drawCentredString(37.5 * mm, height - 35 * mm, settings.tagline)

    c.setFillColor(brand_red)
    c.setFont(ROBOTO_BOLD, 30)
    c.drawRightString(width - 16 * mm, height - 36 * mm, "RECEIPT")

    # ─── 3. META DATA (Fixed Overlap) ───
    y = height - 76 * mm
    c.setFillColor(text_dark)
    
    # Left Block
    c.setFont(ROBOTO_BOLD, 8)
    c.drawString(margin, y, "PAID BY :")
    c.setFont(ROBOTO_BOLD, 9)
    c.drawString(margin, y - 4 * mm, f"{client_service.client.first_name} {client_service.client.last_name}")
    c.setFont(ROBOTO_BOLD, 7)
    c.drawString(margin, y - 8 * mm, f"REF: {client_service.land_description[:30].upper()}")
    
    # Center Block
    center_x = margin + 45 * mm
    c.setFont(ROBOTO_BOLD, 8)
    c.drawString(center_x, y, f"DATE: {client_service.requested_at.strftime('%d %b %Y').upper()}")
    c.drawString(center_x, y - 4 * mm, f"RECEIPT NO: {client_service.id + 10000}") 

    # Right Block
    total_paid = safe_price(client_service.total_paid)
    c.setFont(ROBOTO_BOLD, 8)
    c.drawRightString(width - margin, y, "TOTAL PAID:")
    c.setFont(ROBOTO_BOLD, 12)
    c.drawRightString(width - margin, y - 5 * mm, f"KSH {total_paid:,.0f}/=")

    # Red Dashed Separator Line
    y -= 13 * mm
    c.setStrokeColor(brand_red)
    c.setLineWidth(1)
    c.setDash(3, 3) 
    c.line(margin, y, width - margin, y)
    c.setDash()

    # ─── 4. TABLE HEADER ───
    y -= 10 * mm
    c.setFillColor(brand_blue)
    c.rect(margin, y, width - (2 * margin), 7 * mm, fill=1, stroke=0)
    
    c.setFillColor(HexColor("#FFFFFF"))
    c.setFont(ROBOTO_BOLD, 8)
    
    col_desc = margin + 2 * mm
    col_rate = width - 75 * mm
    col_qty  = width - 55 * mm
    col_tot  = width - 35 * mm
    col_paid = width - margin - 2 * mm

    c.drawString(col_desc, y + 2 * mm, "Description")
    c.drawRightString(col_rate, y + 2 * mm, "Rate")
    c.drawRightString(col_qty, y + 2 * mm, "Quantity")
    c.drawRightString(col_tot, y + 2 * mm, "Total")
    c.drawRightString(col_paid, y + 2 * mm, "Paid")

    y -= 7 * mm

    # ─── 5. TABLE ROWS (Tighter padding to preserve footer) ───
    c.setFillColor(text_dark)
    c.setFont(ROBOTO_BOLD, 8) 
    
    if client_service.service.category == ServiceCategory.TITLE:
        for csp in client_service.service_processes.all().order_by('process__step_order'):
            name = csp.process.name[:35]
            price = safe_price(csp.overridden_cost if csp.overridden_cost is not None else csp.process.cost)
            paid  = safe_price(csp.paid_amount)
            
            c.drawString(col_desc, y, name)
            c.drawRightString(col_rate, y, f"{price:,.0f}")
            c.drawRightString(col_qty, y, "-")
            c.drawRightString(col_tot, y, f"{price:,.0f}")
            c.drawRightString(col_paid, y, f"{paid:,.0f}")
            y -= 6.5 * mm
    else:
        name  = client_service.service.name[:35]
        raw_price = client_service.overridden_total_price if client_service.overridden_total_price is not None else client_service.service.total_price
        price = safe_price(raw_price)
        paid  = safe_price(client_service.total_paid)
        
        c.drawString(col_desc, y, name)
        c.drawRightString(col_rate, y, f"{price:,.0f}")
        c.drawRightString(col_qty, y, "-")
        c.drawRightString(col_tot, y, f"{price:,.0f}")
        c.drawRightString(col_paid, y, f"{paid:,.0f}")
        y -= 6.5 * mm

    if client_service.sub_services.exists():
        for css in client_service.sub_services.all():
            name = css.sub_service.name[:35]
            price = safe_price(css.overridden_price if css.overridden_price is not None else css.sub_service.price)
            paid  = safe_price(css.paid_amount)
            
            c.drawString(col_desc, y, name)
            c.drawRightString(col_rate, y, f"{price:,.0f}")
            c.drawRightString(col_qty, y, "-")
            c.drawRightString(col_tot, y, f"{price:,.0f}")
            c.drawRightString(col_paid, y, f"{paid:,.0f}")
            y -= 6.5 * mm

    # ─── 6. SUBTOTAL & FINAL TOTAL ───
    y -= 2 * mm
    c.setFillColor(bg_gray)
    c.rect(margin, y - 3 * mm, width - (2 * margin), 6 * mm, fill=1, stroke=0)
    
    total_price_val = safe_price(client_service.overridden_total_price if client_service.overridden_total_price is not None else client_service.service.total_price)
    
    c.setFillColor(text_dark)
    c.setFont(ROBOTO_BOLD, 9)
    c.drawString(col_desc, y - 1 * mm, "SUB TOTAL")
    c.drawRightString(col_rate, y - 1 * mm, f"{total_price_val:,.0f}")
    c.drawRightString(col_qty, y - 1 * mm, "-")
    c.drawRightString(col_tot, y - 1 * mm, f"{total_price_val:,.0f}")
    c.drawRightString(col_paid, y - 1 * mm, f"{total_paid:,.0f}")

    y -= 10 * mm
    c.setFillColor(bg_gray)
    c.rect(margin, y - 3 * mm, width - (2 * margin), 6 * mm, fill=1, stroke=0)
    
    c.setFillColor(brand_red)
    c.setFont(ROBOTO_BOLD, 9)
    c.drawString(col_desc, y - 1 * mm, "Total")
    
    c.setStrokeColor(brand_red)
    c.setLineWidth(1.5)
    c.line(col_desc, y - 2 * mm, col_desc + 10 * mm, y - 2 * mm) 
    
    c.drawRightString(col_qty, y - 1 * mm, "-")
    c.drawRightString(col_tot, y - 1 * mm, f"{total_price_val:,.0f}") 
    c.drawRightString(col_paid, y - 1 * mm, f"{total_paid:,.0f}") 

    # ─── 7. FOOTER AREA (Pulled Up To Guarantee Visibility) ───
    y_footer = y - 20 * mm 
    
    c.setFillColor(brand_green)
    c.roundRect(margin, y_footer, 35 * mm, 5 * mm, 1.5 * mm, fill=1, stroke=0)
    c.setFillColor(HexColor("#FFFFFF"))
    c.setFont(ROBOTO_BOLD, 8)
    c.drawString(margin + 3 * mm, y_footer + 1.2 * mm, "Payment Methods")

    y_p = y_footer - 5 * mm
    c.setFillColor(text_dark)
    c.setFont(ROBOTO_BOLD, 7.5)
    c.drawString(margin, y_p, "Bank Name : ")
    c.setFillColor(brand_green)
    c.drawString(margin + 18*mm, y_p, config['bank_name'])

    y_p -= 4 * mm
    c.setFillColor(text_dark)
    c.drawString(margin, y_p, "M-PESA PAYBILL : ")
    c.setFillColor(brand_green)
    c.drawString(margin + 26*mm, y_p, config['paybill'])

    y_p -= 4 * mm
    c.setFillColor(text_dark)
    c.drawString(margin, y_p, "Account No: ")
    c.setFillColor(brand_green)
    c.drawString(margin + 18*mm, y_p, config['account_no'])

    y_p -= 4 * mm
    c.setFillColor(text_dark)
    c.drawString(margin, y_p, "Account Name: ")
    c.setFillColor(brand_red)
    c.drawString(margin + 22*mm, y_p, config['account_name'])

    y_p -= 4 * mm
    c.setFillColor(brand_green)
    c.drawString(margin, y_p, f"Mpesa : {config['mpesa_phone']}")

    # Added prominent Balance Box
    total_balance = safe_price(client_service.total_balance)
    c.setFillColor(brand_blue)
    c.rect(width - 55 * mm, y_footer - 1 * mm, 43 * mm, 7 * mm, fill=1, stroke=0)
    c.setFillColor(HexColor("#FFFFFF"))
    c.setFont(ROBOTO_BOLD, 10)
    c.drawString(width - 53 * mm, y_footer + 1 * mm, f"BALANCE : KSH {total_balance:,.0f}")

    y_sig = y_footer - 6 * mm
    c.setFillColor(HexColor("#444444"))
    c.setFont(ROBOTO_LIGHT, 7)
    c.drawString(width - 55 * mm, y_sig, "Expressing gratitude for the privilege of")
    c.drawString(width - 55 * mm, y_sig - 3 * mm, "your business!")
    
    c.setFont(ROBOTO_BOLD, 7)
    c.drawString(width - 55 * mm, y_sig - 9 * mm, f"For {config['account_name']}:")

    sig_drawn = False
    if settings and settings.stamp_signature:
        sig_drawn = draw_image_safe(c, settings.stamp_signature, width - 55 * mm, y_sig - 20 * mm, 30 * mm, 12 * mm)
    
    if not sig_drawn:
        c.setFillColor(brand_blue)
        c.setFont(ROBOTO_BOLD, 14) 
        c.drawCentredString(width - 33 * mm, y_sig - 18 * mm, printed_by_user.first_name)

    c.setFillColor(text_dark)
    c.setFont(ROBOTO_BOLD, 7)
    c.setStrokeColor(text_dark)
    c.setLineWidth(0.5)
    c.setDash(1, 1)
    c.line(width - 55 * mm, y_sig - 24 * mm, width - 12 * mm, y_sig - 24 * mm)
    c.setDash()
    c.drawCentredString(width - 33 * mm, y_sig - 27 * mm, "AUTHORIZED SIGN")

    # ─── 8. ABSOLUTE BOTTOM BARS (Stacked Addresses) ───
    c.setFillColor(text_dark)
    c.setFont(ROBOTO_BOLD, 6)
    # Stack the addresses safely above the blue bar
    c.drawCentredString(width / 2, 16 * mm, config["address_1"])
    c.drawCentredString(width / 2, 13 * mm, config["address_2"])

    c.setFillColor(brand_blue)
    c.rect(0, 7 * mm, width, 4.5 * mm, fill=1, stroke=0)
    c.setFillColor(HexColor("#FFFFFF"))
    c.setFont(ROBOTO_BOLD, 6.5)
    c.drawCentredString(width / 2, 8.2 * mm, config["services_list"])

    c.setFillColor(brand_gold)
    c.rect(0, 0, width * 0.55, 7 * mm, fill=1, stroke=0)
    c.setFillColor(brand_green)
    c.rect(width * 0.55, 0, width * 0.45, 7 * mm, fill=1, stroke=0)
    
    c.setFillColor(brand_blue)
    c.setFont(ROBOTO_BOLD, 7.5)
    c.drawString(margin, 2 * mm, config["website"])
    
    c.setFillColor(HexColor("#FFFFFF"))
    c.drawRightString(width - margin, 2 * mm, config["mpesa_phone"])

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer

# ─── DJANGO VIEWS ─────────────────────────────────────────────────────────────
@login_required
def download_receipt(request, cs_id):
    cs = get_object_or_404(ClientService, id=cs_id)
    buf = generate_service_receipt(cs, request.user)

    response = HttpResponse(buf, content_type='application/pdf')
    filename = f"receipt_{cs.id}.pdf"
    # Allow inline display in iframe
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response
