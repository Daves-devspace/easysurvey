# apps/accounts/views.py

from io import BytesIO
from decimal import Decimal
import logging
from datetime import datetime, timedelta

from django.http import HttpResponse
from django.views import View
from django.utils import timezone
from django.db.models import Q

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib.styles import getSampleStyleSheet

from .models import CashbookEntry
from .services.opening_balance import get_opening_summary

logger = logging.getLogger(__name__)


class DailyCashbookPDFView(View):
    """
    Robust read-only PDF for daily cashbook audit.
    Uses multiple fallbacks to find entries for the selected day and logs diagnostics.
    """

    def color_to_hex(self, color):
        r = int(color.red * 255)
        g = int(color.green * 255)
        b = int(color.blue * 255)
        return f"#{r:02X}{g:02X}{b:02X}"

    def parse_date_param(self, date_str):
        """
        Accepts YYYY-MM-DD (preferred), or common human formats as fallback.
        Returns a date object or None.
        """
        if not date_str:
            return None
        # Try strict iso first
        try:
            return timezone.datetime.strptime(date_str, "%Y-%m-%d").date()
        except Exception:
            pass
        # Try some other common formats (e.g. "Oct. 7, 2025" or "Oct 7, 2025")
        for fmt in ("%b. %d, %Y", "%b %d, %Y", "%d %b %Y"):
            try:
                return timezone.datetime.strptime(date_str, fmt).date()
            except Exception:
                continue
        # last resort: try parsing using dateutil if available
        try:
            from dateutil.parser import parse as dateutil_parse
            return dateutil_parse(date_str).date()
        except Exception:
            return None

    def get(self, request, *args, **kwargs):
        raw_date = request.GET.get("date")
        logger.info("PDF request received. raw date param: %r, requester=%s", raw_date, getattr(request, "user", None))

        parsed = self.parse_date_param(raw_date)
        if parsed is None:
            selected_date = timezone.now().date()
            logger.warning("Could not parse date param %r; defaulting to today (%s)", raw_date, selected_date)
        else:
            selected_date = parsed

        logger.info("Generating daily cashbook PDF for date: %s", selected_date)

        # Opening summary (read-only)
        opening_summary = get_opening_summary(selected_date)
        opening_balance = Decimal(opening_summary.get("flagged", Decimal("0.00")))
        delta = Decimal(opening_summary.get("delta", Decimal("0.00")))
        logger.debug("Opening summary: %s", opening_summary)

        # 1) Primary: by entry_date
        qs = CashbookEntry.objects.filter(entry_date=selected_date).order_by("created_at")
        primary_count = qs.count()
        logger.info("Primary query (entry_date=%s) returned %d", selected_date, primary_count)

        entries = list(qs)
        fallback_used = False

        # 2) Simple fallback: created_at__date
        if primary_count == 0:
            qs2 = CashbookEntry.objects.filter(created_at__date=selected_date).order_by("created_at")
            count2 = qs2.count()
            logger.info("Fallback query (created_at__date=%s) returned %d", selected_date, count2)
            if count2 > 0:
                entries = list(qs2)
                fallback_used = True

        # 3) Timezone-aware fallback: created_at between local-day start and end
        if not entries:
            tz = timezone.get_current_timezone()
            # create aware range covering that local calendar day
            start_naive = datetime(selected_date.year, selected_date.month, selected_date.day, 0, 0, 0)
            start = timezone.make_aware(start_naive, timezone=tz)
            end = start + timedelta(days=1)
            qs3 = CashbookEntry.objects.filter(created_at__gte=start, created_at__lt=end).order_by("created_at")
            count3 = qs3.count()
            logger.info("Range fallback (created_at between %s and %s) returned %d", start, end, count3)
            if count3 > 0:
                entries = list(qs3)
                fallback_used = True

        # Log a few samples for quick diagnosis
        sample = []
        for e in entries[:8]:
            sample.append({"pk": e.pk, "entry_date": e.entry_date, "created_at": e.created_at.isoformat(), "type": e.entry_type, "amt": str(e.amount), "desc": (e.description or "")[:60]})
        logger.debug("Sample entries: %s", sample)

        # Decimal-safe sums
        today_in = sum((e.amount for e in entries if e.entry_type == "IN"), Decimal("0.00"))
        today_out = sum((e.amount for e in entries if e.entry_type == "OUT"), Decimal("0.00"))
        closing_balance = opening_balance + today_in - today_out

        logger.info("KPIs for %s -> opening=%s delta=%s in=%s out=%s closing=%s (fallback_used=%s)",
                    selected_date, opening_balance, delta, today_in, today_out, closing_balance, fallback_used)

        # build PDF (same visual as before)
        cash_flow_ratio = None
        ratio_display = "N/A"
        if today_out > Decimal("0.00"):
            try:
                cash_flow_ratio = float(today_in) / float(today_out)
                ratio_display = f"{round(cash_flow_ratio)}×"
            except Exception:
                logger.exception("Error computing ratio")

        flow_color = colors.green if today_in >= today_out else colors.red
        ratio_color = colors.green if cash_flow_ratio and cash_flow_ratio >= 1 else colors.red
        warning_text = "⚠" if today_out > today_in else ""

        flow_hex = self.color_to_hex(flow_color)
        ratio_hex = self.color_to_hex(ratio_color)
        out_hex = "#FF0000" if today_out > today_in else "#000000"
        delta_color = colors.red if delta != Decimal("0.00") else colors.black
        delta_hex = self.color_to_hex(delta_color)
        delta_text = f"{delta:+.2f}" if delta != Decimal("0.00") else ""

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=20, leftMargin=20, topMargin=40, bottomMargin=20)
        styles = getSampleStyleSheet()
        normal = styles["Normal"]
        elements = []

        elements.append(Paragraph(f"<b>Daily Cashbook Audit - {selected_date.isoformat()}</b>", styles["Title"]))

        opening_html = f"<b>Opening Balance (Flagged)</b><br/>{opening_balance:.2f}"
        if delta_text:
            opening_html += f"<br/><font color='{delta_hex}'>Out of sync: {delta_text}</font>"

        kpi_data = [[
            Paragraph(opening_html, normal),
            Paragraph(f"<b>Total Cash In</b><br/><font color='{flow_hex}'>{today_in:.2f}</font>", normal),
            Paragraph(f"<b>Total Cash Out</b><br/><font color='{out_hex}'>{today_out:.2f}</font>", normal),
            Paragraph(f"<b>Closing Balance</b><br/>{closing_balance:.2f}", normal),
            Paragraph(f"<b>In/Out Ratio</b><br/><font color='{ratio_hex}'>{ratio_display} {warning_text}</font>", normal),
        ]]
        kpi_table = Table(kpi_data, colWidths=[40*mm,30*mm,30*mm,35*mm,40*mm])
        kpi_table.setStyle(TableStyle([
            ("BOX", (0,0), (-1,-1), 1, colors.black),
            ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
            ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),
        ]))
        elements.append(kpi_table)
        elements.append(Paragraph("<br/>", normal))

        table_data = [["Time", "Type", "Description", "Amount", "Balance"]]
        for e in entries:
            desc = (e.description or "").replace("\n", "<br/>")
            table_data.append([
                (e.created_at.strftime("%H:%M") if e.created_at else ""),
                e.entry_type,
                Paragraph(desc, normal),
                f"{e.amount:.2f}",
                f"{e.balance_after:.2f}"
            ])

        table = Table(table_data, colWidths=[25*mm,20*mm,85*mm,30*mm,30*mm])
        table.setStyle(TableStyle([
            ("BOX", (0,0), (-1,-1), 0.5, colors.black),
            ("GRID", (0,0), (-1,-1), 0.25, colors.grey),
            ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),
            ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("ALIGN", (3,1), (-1,-1), "RIGHT"),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
            ("WORDWRAP", (2,1), (2,-1), "CJK"),
        ]))
        elements.append(table)

        try:
            doc.build(elements)
        except Exception:
            logger.exception("PDF build failed for date %s", selected_date)

        buffer.seek(0)
        logger.info("Returning PDF for %s (entries=%d, fallback_used=%s)", selected_date, len(entries), fallback_used)
        return HttpResponse(buffer, content_type="application/pdf")
