from datetime import datetime

from django.http import JsonResponse
from django.utils import timezone
from django.views import View
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.models import User
from django.views.generic import TemplateView

from apps.EasyDocs.forms import BookingManageForm
from apps.EasyDocs.models import BookingAssignment, Booking
from apps.Employee.models import EmployeeProfile

# bookings/utils.py
from django.db.models import Count
from django.utils import timezone


from datetime import datetime
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.http import JsonResponse
from django.views import View

def get_calendar_events(start=None, end=None, include_handled=True):
    qs = Booking.objects.all()
    if not include_handled:
        qs = qs.filter(handled=False)

    # Parse incoming start/end into date objects
    if start and end:
        sdate = datetime.fromisoformat(start).date()
        edate = datetime.fromisoformat(end).date()
        qs = qs.filter(scheduled_date__date__range=(sdate, edate))

    # 1) Truncate to date
    qs = qs.annotate(day=TruncDate('scheduled_date'))

    # 2) Group by day + handled, count IDs
    summary = (
        qs
        .values('day', 'handled')
        .annotate(count=Count('id'))
        .order_by('day')
    )

    # 3) Build your calendar‐friendly dicts
    return [
        {
            'date':    entry['day'].isoformat(),
            'count':   entry['count'],
            'handled': entry['handled'],
        }
        for entry in summary
    ]




class BookingCalendarJSON(View):
    """
    GET params:
      - handled  : '1' or '0' (optional, default '1')
      - summary  : '1' (default) for aggregates, '0' for detail
      - start,end: ISO datetimes for calendar range (optional in detail mode)
    """

    def get(self, request):
        include_handled = request.GET.get('handled', '1') == '1'
        summary = request.GET.get('summary', '1') == '1'
        start_iso = request.GET.get('start')
        end_iso = request.GET.get('end')

        qs = Booking.objects.all()
        if not include_handled:
            qs = qs.filter(handled=False)

        errors = []

        if summary:
            if start_iso and end_iso:
                try:
                    sdate = datetime.fromisoformat(start_iso).date()
                    edate = datetime.fromisoformat(end_iso).date()
                except ValueError:
                    errors.append("`start` and `end` must be ISO datetimes")
                else:
                    qs = qs.filter(scheduled_date__date__range=(sdate, edate))

            if errors:
                return JsonResponse({'errors': errors}, status=400)

            qs = qs.annotate(day=TruncDate('scheduled_date'))
            summary_qs = (
                qs
                .values('day', 'handled')
                .annotate(count=Count('id'))
                .order_by('day')
            )

            payload = []
            for entry in summary_qs:
                day_str = entry['day'].isoformat()
                details = Booking.objects.filter(
                    scheduled_date__date=entry['day'],
                    handled=entry['handled']
                ).order_by('scheduled_date').values_list(
                    'client_service__client__first_name',
                    'client_service__service__name'
                )
                detail_list = [f"{fn} – {svc}" for fn, svc in details]

                payload.append({
                    'title': f"{entry['count']} booking{'s' if entry['count'] != 1 else ''}",
                    'start': day_str,
                    'color': '#28a745' if entry['handled'] else '#dc3545',
                    'extendedProps': {'details': detail_list}
                })
            return JsonResponse(payload, safe=False)

        else:
            if start_iso and end_iso:
                try:
                    start_dt = datetime.fromisoformat(start_iso)
                    end_dt = datetime.fromisoformat(end_iso)
                except ValueError:
                    errors.append("`start` and `end` must be ISO datetimes")
                else:
                    qs = qs.filter(scheduled_date__gte=start_dt, scheduled_date__lt=end_dt)

            if errors:
                return JsonResponse({'errors': errors}, status=400)

            events = []
            for b in qs.order_by('scheduled_date'):
                events.append({
                    'id': b.id,
                    'title': f"{b.client_service.client.first_name} – {b.client_service.service.name}",
                    'start': b.scheduled_date.isoformat(),
                    'color': '#28a745' if b.handled else '#dc3545',
                    'extendedProps': {
                        'dispatchMessage': b.dispatch_message or '',
                        'client_id': b.client_service.client.id,
                        'client': b.client_service.client.first_name,
                        'service': b.client_service.service.name,
                        'handled': b.handled,
                        'time': b.scheduled_date.time().strftime('%H:%M')
                    }
                })
            return JsonResponse(events, safe=False)






# services/bookings.py



class BookingManagementView(View):
    """
    Renders the main booking-management page with calendar and
    inline modals for assign/handle actions.
    """
    template_name = 'Management/bookings/booking_management.html'

    def get(self, request):
        today = timezone.localdate()
        unhandled = Booking.objects.filter(handled=False)
        handled   = Booking.objects.filter(handled=True)

        # All surveyors (for both modals)
        surveyors = User.objects.filter(
            employeeprofile__role=EmployeeProfile.RoleChoices.SURVEYOR
        )

        # Build a map: booking_id -> [assigned_surveyor_ids]
        assignments = BookingAssignment.objects.filter(booking__in=unhandled)
        assigned_ids_map = {}
        for a in assignments:
            assigned_ids_map.setdefault(a.booking_id, []).append(a.surveyor_id)

        return render(request, self.template_name, {
            'today_bookings':    unhandled.filter(scheduled_date__date=today),
            'unhandled_bookings': unhandled,
            'handled_bookings':   handled,
            'surveyors':          surveyors,
            'assigned_ids_map':   assigned_ids_map,
        })


class AssignSurveyorsView(View):
    """
    Only assigns surveyors—does NOT mark handled.
    """
    template_name = 'Management/bookings/assign_surveyors_modal.html'

    def get(self, request, pk):
        booking      = get_object_or_404(Booking, pk=pk)
        surveyors    = User.objects.filter(employeeprofile__role=EmployeeProfile.RoleChoices.SURVEYOR)
        assigned_ids = booking.bookingassignment_set.values_list('surveyor_id', flat=True)
        return render(request, self.template_name, {
            'booking': booking,
            'surveyors': surveyors,
            'assigned_ids': assigned_ids,
        })

    def post(self, request, pk):
        booking     = get_object_or_404(Booking, pk=pk)
        surveyor_ids = request.POST.getlist('surveyors')

        # Clear old assignments, add new ones
        booking.bookingassignment_set.all().delete()
        for uid in surveyor_ids:
            BookingAssignment.objects.create(booking=booking, surveyor_id=uid)

        messages.success(request, "✅ Surveyors assigned successfully.")
        return redirect('booking-management')


class MarkBookingHandledView(View):
    """
    Only marks a booking as handled and allows final surveyor selection.
    """
    template_name = 'Management/bookings/mark_handled_modal.html'

    def get(self, request, pk):
        booking      = get_object_or_404(Booking, pk=pk)
        surveyors    = User.objects.filter(employeeprofile__role=EmployeeProfile.RoleChoices.SURVEYOR)
        assigned_ids = booking.bookingassignment_set.values_list('surveyor_id', flat=True)
        return render(request, self.template_name, {
            'booking': booking,
            'surveyors': surveyors,
            'assigned_ids': assigned_ids,
        })

    def post(self, request, pk):
        booking      = get_object_or_404(Booking, pk=pk)
        surveyor_ids = request.POST.getlist('surveyors')

        # Final assignments
        booking.bookingassignment_set.all().delete()
        for uid in surveyor_ids:
            BookingAssignment.objects.create(booking=booking, surveyor_id=uid)

        # Mark as handled
        booking.handled     = True
        booking.handled_at  = timezone.now()
        booking.handled_by  = request.user
        booking.save(update_fields=['handled','handled_at','handled_by'])

        messages.success(request, "✅ Booking marked as handled.")
        return redirect('booking-management')
