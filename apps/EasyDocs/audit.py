from django.utils.dateparse import parse_datetime
from django.views.generic import TemplateView
from django.shortcuts import render
from django.utils import timezone
from django.utils.html import format_html
from apps.EasyDocs.models import AuditLog


def render_audit_logs(queryset=None):
    """
    Utility to fetch and render audit logs with HTML badges for timestamps.
    """
    if queryset is None:
        queryset = AuditLog.objects.select_related("user").order_by("-timestamp")

    logs = []
    now = timezone.now()

    for log in queryset:
        # Age in seconds
        delta = (now - log.timestamp).total_seconds()

        # Pick badge color based on recency
        if delta < 3600:  # <1 hour
            badge_class = "bg-danger"
        elif delta < 86400:  # <24 hours
            badge_class = "bg-warning text-dark"
        else:
            badge_class = "bg-secondary"

        timestamp_badge = format_html(
            '<span class="badge {}">{}</span>',
            badge_class,
            log.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        )

        logs.append({
            "id": log.id,
            "timestamp_badge": timestamp_badge,
            "user": log.user,
            "action": log.get_action_display(),
            "model": log.model_name,
            "object_id": log.object_id,
            "description": log.description or "",
            "ip": log.ip_address or "-",
            "agent": (log.user_agent[:30] + "...") if log.user_agent else "-",
        })

    return {"logs": logs,
            "action_choices": AuditLog.ACTION_CHOICES}


class AuditLogListView(TemplateView):
    template_name = "Management/audit_logs.html"

    def get_queryset(self, request):
        queryset = AuditLog.objects.select_related("user").order_by("-timestamp")

        # Role-based scoping
        if not request.user.is_superuser:
            queryset = queryset.filter(user=request.user)

        # Optional filters
        action = request.GET.get("action")
        if action:
            queryset = queryset.filter(action=action)

        start_date = request.GET.get("start_date")
        end_date = request.GET.get("end_date")

        if start_date:
            try:
                start = timezone.make_aware(parse_datetime(start_date))
                queryset = queryset.filter(timestamp__gte=start)
            except Exception:
                pass

        if end_date:
            try:
                end = timezone.make_aware(parse_datetime(end_date))
                queryset = queryset.filter(timestamp__lte=end)
            except Exception:
                pass

        return queryset

    def get(self, request, *args, **kwargs):
        queryset = self.get_queryset(request)
        data = render_audit_logs(queryset=queryset)

        if request.headers.get("HX-Request"):  # HTMX partial
            return render(request, "Management/partials/audit_logs_table.html", data)

        return render(request, self.template_name, data)
