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
        os.path.join(django_settings.STATIC_ROOT, 'fonts', 'Roboto-Regular.ttf') if getattr(django_settings, 'STATIC_ROOT', None) else None,
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


def draw_icon_image(c, image_filename, x, y, width, height):
    """Attempts to draw a PNG icon from Django static files. Returns False if missing."""
    try:
        # Check standard Django static directories for the icons
        image_paths = [
            os.path.join(django_settings.BASE_DIR, 'static', 'assets', 'images', image_filename),
            os.path.join(django_settings.BASE_DIR, 'staticfiles', 'assets', 'images', image_filename),
            os.path.join(django_settings.STATIC_ROOT, 'assets', 'images', image_filename) if getattr(django_settings, 'STATIC_ROOT', None) else None,
            os.path.join(django_settings.BASE_DIR, 'static', 'images', image_filename),
            os.path.join(django_settings.BASE_DIR, 'staticfiles', 'images', image_filename),
        ]
        
        path = next((p for p in image_paths if p and os.path.exists(p)), None)
        
        if path:
            img = ImageReader(path)
            c.drawImage(img, x, y, width, height, mask='auto', preserveAspectRatio=True)
            return True
    except Exception as e:
        logger.warning(f"Error loading icon {image_filename}: {e}")
        
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
        "account_name": "GEOSPOT SURVEYS",
        "account_no": "7812470",
        "paybill": "522533",
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
    # Top Accents
    c.setFillColor(brand_gold)
    c.rect(0, height - 6 * mm, width, 6 * mm, fill=1, stroke=0)
    c.rect(width - 6 * mm, height - 40 * mm, 6 * mm, 40 * mm, fill=1, stroke=0)

    # REFINED/REDUCED BLUE POLYGON
    c.setFillColor(brand_blue)
    p = c.beginPath()
    p.moveTo(0, height)
    p.lineTo(55 * mm, height)               
    p.lineTo(55 * mm, height - 25 * mm)     
    p.lineTo(0, height - 40 * mm)           
    p.close()
    c.drawPath(p, fill=1, stroke=0)

    base_y = height - 35 * mm
    h = 14 * mm
    p = c.beginPath()
    p.moveTo(width - 6 * mm, base_y - (h * 0.15))
    p.lineTo(width, base_y - (h * 0.35))
    p.lineTo(width, base_y - h)
    p.lineTo(width - 6 * mm, base_y - (h * 0.80))
    p.close()
    c.drawPath(p, fill=1, stroke=0)

    # ─── NEW EXACT BOTTOM SHAPES ───
    # 1. Solid Gold Base at the absolute bottom (Now stops at 0 to fill the white space)
    c.setFillColor(brand_gold)
    p = c.beginPath()
    p.moveTo(0, 59 * mm)      
    p.lineTo(width, 29 * mm)  
    p.lineTo(width, 0 * mm)   
    p.lineTo(0, 0 * mm)       
    p.close()
    c.drawPath(p, fill=1, stroke=0)

    # 2. Sleek Blue Diagonal Stripe (TOP/START on the left)
    c.setFillColor(brand_blue)
    p = c.beginPath()
    p.moveTo(0, 59 * mm)      
    p.lineTo(width, 29 * mm)  
    p.lineTo(width, 21 * mm)  
    p.lineTo(0, 51 * mm)      
    p.close()
    c.drawPath(p, fill=1, stroke=0)

    # 3. Thin White Diagonal Stripe (Directly BELOW the Blue stripe)
    c.setFillColor(HexColor("#FFFFFF"))
    p = c.beginPath()
    p.moveTo(0, 51 * mm)      
    p.lineTo(width, 21 * mm)  
    p.lineTo(width, 18 * mm)   
    p.lineTo(0, 48 * mm)
    p.close()
    c.drawPath(p, fill=1, stroke=0)

    # 4. The White Shape covering the Footer Content!
    # Lifted to start at y=4mm leaving a clean gold paper border at the bottom edge
    c.setFillColor(HexColor("#FFFFFF"))
    c.roundRect(5 * mm, 4 * mm, width - 10 * mm, 73 * mm, 3 * mm, fill=1, stroke=0)

    c.restoreState()

    # ─── 2. HEADER CONTENT ───
    logo_drawn = False
    if settings and settings.logo:
        logo_drawn = draw_image_safe(c, settings.logo, 5 * mm, height - 28 * mm, 40 * mm, 24 * mm, preserve_aspect=True)
    
    if not logo_drawn:
        c.setFillColor(HexColor("#FFFFFF"))
        c.setFont(ROBOTO_BOLD, 10.5)
        c.drawCentredString(27.5 * mm, height - 16 * mm, settings.company_name if settings and settings.company_name else "GeoSPOT SURVEYS")
        if settings and settings.tagline:
            c.setFont(ROBOTO, 6.5)
            c.drawCentredString(27.5 * mm, height - 20 * mm, settings.tagline)

    c.setFillColor(brand_red)
    c.setFont(ROBOTO_BOLD, 28)
    c.drawRightString(width - 16 * mm, height - 26 * mm, "RECEIPT")

    # ─── 3. META DATA ───
    y = height - 52 * mm 
    c.setFillColor(text_dark)
    
    client_name = f"{client_service.client.first_name or ''} {client_service.client.last_name or ''}".strip() if client_service.client else "Unknown Client"
    land_desc = (client_service.land_description or "N/A")[:30].upper()
    req_date = client_service.requested_at.strftime('%d %b %Y').upper() if client_service.requested_at else "N/A"
    receipt_no = f"{client_service.id + 10000}" if client_service.id else "N/A"
    
    # Left Block
    c.setFont(ROBOTO_BOLD, 8)
    c.drawString(margin, y, "PAID BY :")
    c.setFont(ROBOTO_BOLD, 9)
    c.drawString(margin, y - 3.5 * mm, client_name)
    
    # Center Block
    center_x = margin + 45 * mm
    c.setFont(ROBOTO_BOLD, 8)
    c.drawString(center_x, y, f"DATE: {req_date}")
    c.drawString(center_x, y - 3.5 * mm, f"RECEIPT NO: {receipt_no}") 
    
    # REF Block
    c.setFillColor(brand_red)
    c.setFont(ROBOTO_BOLD, 10.5) 
    c.drawString(center_x, y - 8.5 * mm, f"REF: {land_desc}")

    # Right Block
    c.setFillColor(text_dark)
    total_paid = safe_price(client_service.total_paid)
    c.setFont(ROBOTO_BOLD, 8)
    c.drawRightString(width - margin, y, "TOTAL PAID:")
    c.setFont(ROBOTO_BOLD, 12)
    c.drawRightString(width - margin, y - 4.5 * mm, f"KSH {total_paid:,.0f}/=")

    # Red Dashed Separator Line
    y -= 12 * mm
    c.setStrokeColor(brand_red)
    c.setLineWidth(1)
    c.setDash(3, 3) 
    c.line(margin, y, width - margin, y)
    c.setDash()

    # ─── 4. TABLE HEADER ───
    y -= 7 * mm
    c.setFillColor(brand_blue)
    c.rect(margin, y, width - (2 * margin), 6 * mm, fill=1, stroke=0)
    
    c.setFillColor(HexColor("#FFFFFF"))
    c.setFont(ROBOTO_BOLD, 8)
    
    col_desc = margin + 2 * mm
    col_rate = width - 75 * mm
    col_qty  = width - 55 * mm
    col_tot  = width - 35 * mm
    col_paid = width - margin - 2 * mm

    c.drawString(col_desc, y + 1.5 * mm, "Description")
    c.drawRightString(col_rate, y + 1.5 * mm, "Rate")
    c.drawRightString(col_qty, y + 1.5 * mm, "Qty")
    c.drawRightString(col_tot, y + 1.5 * mm, "Total")
    c.drawRightString(col_paid, y + 1.5 * mm, "Paid")

    y -= 6 * mm

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
            y -= 5.5 * mm
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
        y -= 5.5 * mm

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
            y -= 5.5 * mm

    # ─── 6. SUBTOTAL & FINAL TOTAL ───
    y -= 1.5 * mm
    c.setFillColor(bg_gray)
    c.rect(margin, y - 2.5 * mm, width - (2 * margin), 5 * mm, fill=1, stroke=0)
    
    base_total = client_service.service.total_price if has_service else 0
    total_price_val = safe_price(client_service.overridden_total_price if client_service.overridden_total_price is not None else base_total)
    
    c.setFillColor(text_dark)
    c.setFont(ROBOTO_BOLD, 9)
    c.drawString(col_desc, y - 1 * mm, "SUB TOTAL")
    c.drawRightString(col_rate, y - 1 * mm, f"{total_price_val:,.0f}")
    c.drawRightString(col_qty, y - 1 * mm, "-")
    c.drawRightString(col_tot, y - 1 * mm, f"{total_price_val:,.0f}")
    c.drawRightString(col_paid, y - 1 * mm, f"{total_paid:,.0f}")

    y -= 8 * mm
    c.setFillColor(bg_gray)
    c.rect(margin, y - 2.5 * mm, width - (2 * margin), 5 * mm, fill=1, stroke=0)
    
    c.setFillColor(brand_red)
    c.setFont(ROBOTO_BOLD, 9)
    c.drawString(col_desc, y - 1 * mm, "Total")
    
    c.setStrokeColor(brand_red)
    c.setLineWidth(1.5)
    c.line(col_desc, y - 2 * mm, col_desc + 10 * mm, y - 2 * mm) 
    
    c.drawRightString(col_qty, y - 1 * mm, "-")
    c.drawRightString(col_tot, y - 1 * mm, f"{total_price_val:,.0f}") 
    c.drawRightString(col_paid, y - 1 * mm, f"{total_paid:,.0f}") 

    # ─── 7. FOOTER AREA (PERMANENTLY ANCHORED) ───
    y_footer = 59 * mm 

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
    mpesa_icon_drawn = draw_icon_image(c, "mpesaicon.png", margin, y_p - 1 * mm, 16 * mm, 4.5 * mm)
    if mpesa_icon_drawn:
        c.drawString(margin + 17 * mm, y_p, "PAYBILL : ")
        c.setFillColor(brand_green)
        c.drawString(margin + 32 * mm, y_p, config['paybill'])
    else:
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

    # Balance Box
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

    printed_by_name = printed_by_user.first_name or printed_by_user.username or "Admin"

    sig_drawn = False
    if settings and settings.stamp_signature:
        sig_drawn = draw_image_safe(c, settings.stamp_signature, width - 55 * mm, y_sig - 20 * mm, 30 * mm, 12 * mm)
    
    if not sig_drawn:
        c.setFillColor(brand_blue)
        c.setFont(ROBOTO_BOLD, 14) 
        c.drawCentredString(width - 33 * mm, y_sig - 19 * mm, printed_by_name)

    c.setFillColor(text_dark)
    c.setFont(ROBOTO_BOLD, 7)
    c.setStrokeColor(text_dark)
    c.setLineWidth(0.5)
    c.setDash(1, 1)
    c.line(width - 55 * mm, y_sig - 24 * mm, width - 12 * mm, y_sig - 24 * mm)
    c.setDash()
    c.drawCentredString(width - 33 * mm, y_sig - 27 * mm, "AUTHORIZED SIGN")

    # ─── 8. ABSOLUTE BOTTOM BARS (Raised by 4mm) ───
    
    # Addresses - Nyahururu Left, Kikuyu Right
    c.setFillColor(text_dark)
    c.setFont(ROBOTO_BOLD, 5.5)
    c.drawString(margin, 23 * mm, config["address_1"])
    c.drawRightString(width - margin, 23 * mm, config["address_2"])

    # Services Bar
    services_text = config["services_list"] + " | Land Conveyancing"
    c.setFillColor(brand_blue)
    c.rect(margin, 15 * mm, width - (2 * margin), 4.5 * mm, fill=1, stroke=0)
    c.setFillColor(HexColor("#FFFFFF"))
    c.setFont(ROBOTO_BOLD, 6.5)
    c.drawCentredString(width / 2, 16.2 * mm, services_text)

    # ─── BOTTOM CONTACT BAR (Perfect Circles & Pills) ───
    icon_y = 8 * mm
    x_pos = margin
    
    social_icons = [
        ("linkedin.png", "in"),
        ("facebook.png", "f"),
        ("instagram.png", "ig"),
        ("twitter.png", "X"), 
        ("tiktok.png", "tt")
    ]
    
    icon_size = 4.5 * mm
    radius = icon_size / 2.0
    
    for img_filename, fallback_text in social_icons:
        c.setFillColor(brand_gold)
        c.circle(x_pos + radius, icon_y - 1 * mm + radius, radius, fill=1, stroke=0)
        
        img_size = 2.8 * mm
        offset = (icon_size - img_size) / 2.0
        img_success = draw_icon_image(c, img_filename, x_pos + offset, icon_y - 1 * mm + offset, img_size, img_size)
        
        if not img_success:
            c.setFillColor(HexColor("#FFFFFF"))
            c.setFont(ROBOTO_BOLD, 6.5)
            c.drawCentredString(x_pos + radius, icon_y + 0.2 * mm, fallback_text)
            
        x_pos += icon_size + 1.2 * mm

    badge_width = 20 * mm
    c.setFillColor(brand_gold)
    c.roundRect(x_pos, icon_y - 1 * mm, badge_width, icon_size, radius, fill=1, stroke=0)
    
    c.setFillColor(HexColor("#FFFFFF"))
    c.setFont(ROBOTO_BOLD, 6.5)
    c.drawCentredString(x_pos + badge_width / 2.0, icon_y + 0.2 * mm, "geospot surveys")
    
    x_pos += badge_width
    
    phone_w = 42 * mm
    phone_x = width - margin - phone_w
    
    c.setFillColor(brand_green)
    c.roundRect(phone_x, icon_y - 1 * mm, phone_w, icon_size, radius, fill=1, stroke=0)
    
    circle_x = phone_x + radius
    circle_y = icon_y - 1 * mm + radius
    c.setFillColor(HexColor("#FFFFFF"))
    c.circle(circle_x, circle_y, radius - 0.4 * mm, fill=1, stroke=0)
    
    img_size = 2.4 * mm
    phone_success = draw_icon_image(c, "phone.png", circle_x - img_size/2.0, circle_y - img_size/2.0, img_size, img_size)
    
    if not phone_success:
        c.setFillColor(brand_green)
        c.setFont(ROBOTO_BOLD, 3.5)
        c.drawCentredString(circle_x, circle_y - 1.2 * mm, "TEL")    
    
    c.setFillColor(HexColor("#FFFFFF"))
    c.setFont(ROBOTO_BOLD, 6.5)
    c.drawRightString(phone_x + phone_w - 3 * mm, icon_y + 0.2 * mm, "0792 944 218 / 0759 618 519")

    website_center_x = x_pos + (phone_x - x_pos) / 2.0
    c.setFillColor(brand_blue)
    c.setFont(ROBOTO_BOLD, 7)
    c.drawCentredString(website_center_x, icon_y + 0.2 * mm, config["website"])

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