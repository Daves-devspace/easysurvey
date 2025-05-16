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






class BookingManagementView(TemplateView):
    template_name = 'Management/bookings/booking_management.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # 1) Define today as a date object
        today = timezone.localdate()

        # 2) Fetch only today’s unhandled bookings
        context['today_bookings'] = Booking.objects.filter(
            scheduled_date__date=today,
            handled=False
        )

        # 3) (Optional) all unhandled for the calendar
        context['unhandled_bookings'] = Booking.objects.filter(handled=False)
        return context

# class BookingManageView(UpdateView):
#     model = Booking
#     form_class = BookingManageForm
#     template_name = 'Management/bookings/booking_management.html'
#     context_object_name = 'booking'
#
#     def form_valid(self, form):
#         # tag who handled it
#         booking = form.save(commit=False)
#         if form.cleaned_data['mark_handled']:
#             booking.handled_by = self.request.user
#         booking.save()
#         form.save_m2m()  # this calls our override to reassign surveyors
#         messages.success(self.request, "Booking updated.")
#         return super().form_valid(form)
#
#     def get_success_url(self):
#         return reverse_lazy('booking-calendar')






class AssignSurveyorsView(View):


    def get(self, request, pk):
        booking = get_object_or_404(Booking, pk=pk)
        surveyors = User.objects.filter(employeeprofile__role=EmployeeProfile.RoleChoices.SURVEYOR)
        assigned_ids = booking.bookingassignment_set.values_list('surveyor_id', flat=True)
        return render(request, self.template_name, {
            'booking': booking,
            'surveyors': surveyors,
            'assigned_ids': assigned_ids,
        })

    def post(self, request, pk):
        booking = get_object_or_404(Booking, pk=pk)
        surveyor_ids = request.POST.getlist('surveyors')

        # Clear old assignments and add new
        booking.bookingassignment_set.all().delete()
        for uid in surveyor_ids:
            BookingAssignment.objects.create(booking=booking, surveyor_id=uid)

        messages.success(request, "Surveyors assigned successfully.")
        return redirect('booking-calendar')  # Or any relevant redirect



class MarkBookingHandledView(View):


    def get(self, request, pk):
        booking = get_object_or_404(Booking, pk=pk)
        surveyors = User.objects.filter(employeeprofile__role=EmployeeProfile.RoleChoices.SURVEYOR)
        assigned_ids = booking.bookingassignment_set.values_list('surveyor_id', flat=True)

        return render(request, self.template_name, {
            'booking': booking,
            'surveyors': surveyors,
            'assigned_ids': assigned_ids,  # Pre-check these in form
        })

    def post(self, request, pk):
        booking = get_object_or_404(Booking, pk=pk)
        surveyor_ids = request.POST.getlist('surveyors')

        # Reassign as final execution team
        booking.bookingassignment_set.all().delete()
        for uid in surveyor_ids:
            BookingAssignment.objects.create(booking=booking, surveyor_id=uid)

        # Mark as handled
        booking.handled = True
        booking.handled_at = timezone.now()
        booking.handled_by = request.user
        booking.save(update_fields=['handled', 'handled_at', 'handled_by'])

        messages.success(request, "Booking marked as handled and surveyors confirmed.")
        return redirect('booking-calendar')
