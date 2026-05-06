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

# ─── FONT REGISTRATION ───
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

# ─── HELPER FUNCTIONS ───
def safe_price(val):
    raw = val if val is not None else 0
    return Decimal(raw).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

def draw_image_safe(c, image_field, x, y, width, height, preserve_aspect=True):
    """Draws Django ImageFields (like Logo and Signature) safely."""
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

def draw_icon_image(c, image_filename, x, y, width, height):
    """Attempts to draw a PNG icon from Django static files. Returns False if missing."""
    try:
        # Check standard Django static directories for the icons
        image_paths = [
            os.path.join(django_settings.BASE_DIR, 'static', 'images', image_filename),
            os.path.join(django_settings.BASE_DIR, 'staticfiles', 'images', image_filename),
            os.path.join(django_settings.STATIC_ROOT, 'images', image_filename) if getattr(django_settings, 'STATIC_ROOT', None) else None,
        ]
        
        path = next((p for p in image_paths if p and os.path.exists(p)), None)
        
        if path:
            img = ImageReader(path)
            c.drawImage(img, x, y, width, height, mask='auto', preserveAspectRatio=True)
            return True
    except Exception as e:
        logger.warning(f"Error loading icon {image_filename}: {e}")
        
    return False

# ─── MAIN GENERATOR ───
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
        "account_name": settings.company_name.upper() if settings and settings.company_name else "GEOSPOT SURVEYS",
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
        logo_drawn = draw_image_safe(c, settings.logo, 10 * mm, height - 42 * mm, 60 * mm, 38 * mm, preserve_aspect=True)
    
    if not logo_drawn:
        c.setFillColor(HexColor("#FFFFFF"))
        c.setFont(ROBOTO_BOLD, 14)
        c.drawCentredString(37.5 * mm, height - 30 * mm, settings.company_name if settings and settings.company_name else "GeoSPOT SURVEYS")
        if settings and settings.tagline:
            c.setFont(ROBOTO, 8)
            c.drawCentredString(37.5 * mm, height - 35 * mm, settings.tagline)

    c.setFillColor(brand_red)
    c.setFont(ROBOTO_BOLD, 30)
    c.drawRightString(width - 16 * mm, height - 36 * mm, "RECEIPT")

    # ─── 3. META DATA ───
    y = height - 76 * mm
    c.setFillColor(text_dark)
    
    client_name = f"{client_service.client.first_name or ''} {client_service.client.last_name or ''}".strip() if client_service.client else "Unknown Client"
    land_desc = (client_service.land_description or "N/A")[:30].upper()
    req_date = client_service.requested_at.strftime('%d %b %Y').upper() if client_service.requested_at else "N/A"
    receipt_no = f"{client_service.id + 10000}" if client_service.id else "N/A"
    
    # Left Block
    c.setFont(ROBOTO_BOLD, 8)
    c.drawString(margin, y, "PAID BY :")
    c.setFont(ROBOTO_BOLD, 9)
    c.drawString(margin, y - 4 * mm, client_name)
    
    # Center Block
    center_x = margin + 45 * mm
    c.setFont(ROBOTO_BOLD, 8)
    c.drawString(center_x, y, f"DATE: {req_date}")
    c.drawString(center_x, y - 4 * mm, f"RECEIPT NO: {receipt_no}") 
    
    # REF Block (Centered, Red, Increased Font Size)
    c.setFillColor(brand_red)
    c.setFont(ROBOTO_BOLD, 10.5) 
    c.drawString(center_x, y - 9.5 * mm, f"REF: {land_desc}")

    # Right Block
    c.setFillColor(text_dark)
    total_paid = safe_price(client_service.total_paid)
    c.setFont(ROBOTO_BOLD, 8)
    c.drawRightString(width - margin, y, "TOTAL PAID:")
    c.setFont(ROBOTO_BOLD, 12)
    c.drawRightString(width - margin, y - 5 * mm, f"KSH {total_paid:,.0f}/=")

    # Red Dashed Separator Line
    y -= 14 * mm
    c.setStrokeColor(brand_red)
    c.setLineWidth(1)
    c.setDash(3, 3) 
    c.line(margin, y, width - margin, y)
    c.setDash()

    # ─── 4. TABLE HEADER ───
    y -= 9 * mm
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

    # ─── 5. TABLE ROWS ───
    c.setFillColor(text_dark)
    c.setFont(ROBOTO_BOLD, 8) 
    
    has_service = client_service.service is not None

    if has_service and client_service.service.category == ServiceCategory.TITLE:
        for csp in client_service.service_processes.all().order_by('process__step_order'):
            name = (csp.process.name or "Process")[:35] if csp.process else "Process"
            price = safe_price(csp.overridden_cost if csp.overridden_cost is not None else csp.process.cost)
            paid  = safe_price(csp.paid_amount)
            
            c.drawString(col_desc, y, name)
            c.drawRightString(col_rate, y, f"{price:,.0f}")
            c.drawRightString(col_qty, y, "-")
            c.drawRightString(col_tot, y, f"{price:,.0f}")
            c.drawRightString(col_paid, y, f"{paid:,.0f}")
            y -= 6.5 * mm
    else:
        name  = (client_service.service.name or "Service")[:35] if has_service else "Unknown Service"
        service_price = client_service.service.total_price if has_service else 0
        raw_price = client_service.overridden_total_price if client_service.overridden_total_price is not None else service_price
        
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
            name = (css.sub_service.name or "Sub-Service")[:35] if css.sub_service else "Sub-Service"
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
    
    base_total = client_service.service.total_price if has_service else 0
    total_price_val = safe_price(client_service.overridden_total_price if client_service.overridden_total_price is not None else base_total)
    
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

    # ─── 7. FOOTER AREA (WITH SAFETY BOUNDARY) ───
    y_footer = y - 18 * mm 
    if y_footer < 54 * mm:
        y_footer = 54 * mm

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
        sig_drawn = draw_image_safe(c, settings.stamp_signature, width - 55 * mm, y_sig - 18 * mm, 30 * mm, 12 * mm)
    
    printed_by_name = printed_by_user.first_name or printed_by_user.username or "Admin"

    if not sig_drawn:
        c.setFillColor(brand_blue)
        c.setFont(ROBOTO_BOLD, 14) 
        c.drawCentredString(width - 33 * mm, y_sig - 16 * mm, printed_by_name)

    c.setFillColor(text_dark)
    c.setFont(ROBOTO_BOLD, 7)
    c.setStrokeColor(text_dark)
    c.setLineWidth(0.5)
    c.setDash(1, 1)
    c.line(width - 55 * mm, y_sig - 20 * mm, width - 12 * mm, y_sig - 20 * mm)
    c.setDash()
    c.drawCentredString(width - 33 * mm, y_sig - 23 * mm, "AUTHORIZED SIGN")

    # ─── 8. ABSOLUTE BOTTOM BARS ───
    
    # Addresses - Nyahururu strictly left, Kikuyu strictly right
    c.setFillColor(text_dark)
    c.setFont(ROBOTO_BOLD, 5.5)
    c.drawString(margin, 19 * mm, config["address_1"])
    c.drawRightString(width - margin, 19 * mm, config["address_2"])

    # Services Bar - Pushed up to leave a white margin below it
    services_text = config["services_list"] + " | Land Conveyancing"
    c.setFillColor(brand_blue)
    c.rect(0, 11 * mm, width, 5 * mm, fill=1, stroke=0)
    c.setFillColor(HexColor("#FFFFFF"))
    c.setFont(ROBOTO_BOLD, 6.5)
    c.drawCentredString(width / 2, 12.2 * mm, services_text)

    # Contact Bar - Absolute bottom (Clean White Background)
    icon_y = 3.5 * mm
    x_pos = margin
    
    # Define images to load and fallback text
    social_icons = [
        ("linkedin.png", "in"),
        ("facebook.png", "f"),
        ("instagram.png", "ig"),
        ("twitter.png", "X"), 
        ("tiktok.png", "tt")
    ]
    
    icon_width = 4.5
    for img_filename, fallback_text in social_icons:
        # Draw Gold Background Box
        c.setFillColor(brand_gold)
        c.roundRect(x_pos, icon_y - 1 * mm, icon_width * mm, 4.5 * mm, 1 * mm, fill=1, stroke=0)
        
        # Try to draw the PNG image. If it fails, draw the fallback text.
        img_success = draw_icon_image(c, img_filename, x_pos + 0.5 * mm, icon_y - 0.5 * mm, 3.5 * mm, 3.5 * mm)
        
        if not img_success:
            c.setFillColor(HexColor("#FFFFFF"))
            c.setFont(ROBOTO_BOLD, 6.5)
            c.drawCentredString(x_pos + (icon_width / 2.0) * mm, icon_y + 0.2 * mm, fallback_text)
            
        x_pos += (icon_width + 1.2) * mm

    # Handle Name (Golden Badge)
    c.setFillColor(brand_gold)
    c.roundRect(x_pos, icon_y - 1 * mm, 20 * mm, 4.5 * mm, 1 * mm, fill=1, stroke=0)
    c.setFillColor(HexColor("#FFFFFF"))
    c.setFont(ROBOTO_BOLD, 6.5)
    c.drawCentredString(x_pos + 10 * mm, icon_y + 0.2 * mm, "geospot surveys")
    
    x_pos += 22 * mm
    
    # Website Text
    c.setFillColor(brand_blue)
    c.setFont(ROBOTO_BOLD, 7)
    c.drawString(x_pos, icon_y + 0.2 * mm, config["website"])
    
    # Phone Numbers (Green Badge)
    phone_w = 40 * mm
    phone_x = width - margin - phone_w
    
    # 1. Solid Green Box
    c.setFillColor(brand_green)
    c.roundRect(phone_x, icon_y - 1 * mm, phone_w, 4.5 * mm, 1 * mm, fill=1, stroke=0)
    
    # 2. White Circle representing the Call Icon boundary
    c.setFillColor(HexColor("#FFFFFF"))
    c.circle(phone_x + 3.5 * mm, icon_y + 1.25 * mm, 1.5 * mm, fill=1, stroke=0)
    
    # 3. Try to draw the Phone PNG inside the circle
    phone_success = draw_icon_image(c, "phone.png", phone_x + 2.5 * mm, icon_y + 0.25 * mm, 2.0 * mm, 2.0 * mm)
    
    if not phone_success:
        c.setFillColor(brand_green)
        c.setFont(ROBOTO_BOLD, 3.5)
        c.drawCentredString(phone_x + 3.5 * mm, icon_y + 0.3 * mm, "TEL")    # Fallback Text
    
    # 4. White Phone Numbers
    c.setFillColor(HexColor("#FFFFFF"))
    c.setFont(ROBOTO_BOLD, 6.5)
    c.drawRightString(phone_x + phone_w - 2 * mm, icon_y + 0.2 * mm, "0792 944 218 / 0759 618 519")

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
