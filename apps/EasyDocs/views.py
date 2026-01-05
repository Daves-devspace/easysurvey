from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ObjectDoesNotExist
from django.db.models import Q, Prefetch, Sum, DecimalField, F, QuerySet

from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.timezone import now
from django.views.decorators.http import require_http_methods

from apps.EasyDocs.forms import ClientForm, ClientServiceForm, TitleDeedCollectionForm, ClientDocumentForm, DocTypeForm, \
    SubServiceForm, ClientSubServiceForm, SiteSettingsForm, SmsProviderTokenForm, \
    ClientSubServiceEditForm, ClientSmsForm
from apps.EasyDocs.models import Client, ClientService, ClientServiceProcess, ClientDoc, DocType, SubService, \
    ClientSubService, SiteSettings, SmsProviderToken, PaymentHistory, Expense, Payment, MessageLog, TitleDeedCollection, \
    Booking, ServiceCategory, Service, Process

from django.views.generic import TemplateView, DetailView, CreateView
from django.shortcuts import redirect

from apps.EasyDocs.accounts.accounts import get_client_payment_history, get_all_payment_history
from .analytics import get_yearly_revenue_data, get_available_years, monthly_company_revenue
from .accounts.revenue import get_revenue_from_payments
from .clients.client_views import get_client_service_summary
from .services.services import apply_client_service_logic
from .models import Service, Process
from .forms import ServiceForm, ProcessForm
from types import SimpleNamespace
from django.contrib import messages
from apps.Employee.utils.mixins import RolePermissionRequiredMixin

import logging


from .utils import MobileSasaAPI
from ..Employee.models import EmployeeProfile, Payroll





logger = logging.getLogger(__name__)




def sessions(request):
    return render(request, "tools/sessions.html")

def map_viewer(request):
    return render(request, "tools/map_viewer.html")

def mutation_tool(request):
    return render(request, "tools/mutation.html")


def mutation_export(request):
    return render(request, "tools/mutation_export.html")

def file_upload(request):
    return render(request, "tools/file_upload.html")

# Create your views here.
# utils.py (or wherever your function lives)

# def chart_data(request):
#     year = int(request.GET.get('year', date.today().year))
#     data = get_yearly_revenue_data(year)
#     return JsonResponse(data)

def chart_data(request):
    year_str = request.GET.get('year')
    try:
        year = int(year_str)
    except (TypeError, ValueError):
        year = date.today().year  # fallback to current year

    data = get_yearly_revenue_data(year)
    return JsonResponse(data)

# def stacked_service_data(request):
#     year = int(request.GET.get("year", timezone.now().year))
#     chart_data = get_monthly_service_data(year)
#     return JsonResponse(chart_data)


def get_years(request):
    years = get_available_years()
    return JsonResponse({'years': years})


import calendar
from collections import OrderedDict
from datetime import date
from decimal import Decimal

from django.db.models import Sum, Count, F
from django.db.models.functions import TruncMonth, Coalesce
from django.views.generic import TemplateView


def pct_growth(current, previous) -> Decimal:
    """
    Calculate percentage growth.
    Returns 100.00 if previous is zero or missing.
    Handles string inputs gracefully.
    """
    try:
        current = Decimal(current)
    except (InvalidOperation, TypeError):
        current = Decimal('0')

    try:
        previous = Decimal(previous)
    except (InvalidOperation, TypeError):
        previous = Decimal('0')

    if previous > 0:
        return ((current - previous) / previous * 100).quantize(Decimal('0.01'))
    return Decimal('100.00')


def aggregate_ytd(model_or_qs, date_field: str, amount_field: str, start_date, end_date) -> Decimal:
    """
    Sum `amount_field` between start_date and end_date.
    Accepts either:
      - a Model class (you’ll get model.objects.filter)
      - or a pre‑filtered QuerySet (it will further .filter on that)
    """
    if isinstance(model_or_qs, QuerySet):
        qs = model_or_qs
    else:
        # assume it’s a Model class
        qs = model_or_qs.objects.all()

    qs = qs.filter(**{f"{date_field}__range": (start_date, end_date)})
    return qs.aggregate(total=Sum(amount_field))['total'] or Decimal('0.00')


def monthly_series(source, date_field: str, agg_field: str, year: int):
    """
    Build a 12-element list (Jan→Dec).
    - If agg_field == 'id' -> returns integers (counts).
    - Otherwise -> returns Decimals (sum), quantized to 2 decimals.
    Accepts either a Model class or a QuerySet.
    """
    if isinstance(source, QuerySet):
        qs = source
    else:
        qs = source.objects.all()

    qs = (
        qs
        .filter(**{f"{date_field}__year": year})
        .annotate(month_trunc=TruncMonth(date_field))
    )

    aggregate_expr = Count('id') if agg_field == 'id' else Sum(agg_field)
    month_vals = (
        qs
        .values('month_trunc')
        .annotate(val=aggregate_expr)
        .order_by('month_trunc')
    )

    # Initialize months structure: ints for counts, Decimals for sums
    if agg_field == 'id':
        data = OrderedDict((calendar.month_abbr[m], 0) for m in range(1, 13))
    else:
        data = OrderedDict((calendar.month_abbr[m], Decimal('0.00')) for m in range(1, 13))

    for entry in month_vals:
        mon = entry['month_trunc'].month if entry.get('month_trunc') else None
        if not mon:
            continue
        key = calendar.month_abbr[mon]
        val = entry['val'] or 0
        if agg_field == 'id':
            data[key] = int(val)
        else:
            # Ensure Decimal and quantize to 2 dp for consistent math/formatting
            data[key] = Decimal(val).quantize(Decimal('0.01'))

    return list(data.values())



class DashboardView(TemplateView):
    template_name = "dashboard.html"

    def get_context_data(self, **kwargs):
        """
        Gathers all statistics for the main admin dashboard.
        """
        context = super().get_context_data(**kwargs)
        
        # 1. Establish the timeframe
        today = timezone.localdate()
        current_year = today.year
        prev_year = current_year - 1

        # ─────────────────────────────────────────────
        # Revenue Metrics (Flow Metric: Year-to-Date)
        # ─────────────────────────────────────────────
        # We look at money made THIS year vs money made LAST year up to this specific date.
        rev_cur = get_revenue_from_payments(year=current_year, up_to_date=today)
        rev_prev = get_revenue_from_payments(year=prev_year, up_to_date=today)

        rev_cur_gross = rev_cur['gross_total']
        rev_cur_net = rev_cur['company_total']
        rev_cur_inst = rev_cur['inst_total']

        rev_prev_gross = rev_prev['gross_total']
        rev_prev_net = rev_prev['company_total']
        rev_prev_inst = rev_prev['inst_total']

        context.update({
            'rev_cur_gross': rev_cur_gross,
            'rev_prev_gross': rev_prev_gross,
            'rev_cur_net': rev_cur_net,
            'rev_prev_net': rev_prev_net,
            'rev_cur_inst_paid': rev_cur_inst,
            'rev_prev_inst_paid': rev_prev_inst,
            'rev_growth_pct': pct_growth(rev_cur_net, rev_prev_net),
            # Quantize ensures we display 2 decimal places in the template
            'rev_diff': (rev_cur_net - rev_prev_net).quantize(Decimal('0.01')),
        })

        # ─────────────────────────────────────────────
        # Expenses (Flow Metric: Year-to-Date)
        # ─────────────────────────────────────────────
        # 1. Payroll: Salaries paid this year
        payroll_cur = Payroll.objects.filter(
            month__year=current_year, is_paid=True
        ).aggregate(total=Sum('net_salary'))['total'] or Decimal('0.00')

        payroll_prev = Payroll.objects.filter(
            month__year=prev_year,
            month__month__lte=today.month,
            month__day__lte=today.day,
            is_paid=True
        ).aggregate(total=Sum('net_salary'))['total'] or Decimal('0.00')

        # 2. Sub Services: Costs incurred from service providers
        ss_cur = ClientSubService.objects.annotate(
            amt=Coalesce('overridden_price', F('sub_service__price'))
        ).filter(
            added_on__year=current_year
        ).aggregate(total=Sum('amt'))['total'] or Decimal('0.00')

        ss_prev = ClientSubService.objects.annotate(
            amt=Coalesce('overridden_price', F('sub_service__price'))
        ).filter(
            added_on__year=prev_year,
            added_on__month__lte=today.month,
            added_on__day__lte=today.day
        ).aggregate(total=Sum('amt'))['total'] or Decimal('0.00')

        # 3. General Expenses: Office costs, bills, etc.
        ge_cur = Expense.objects.filter(
            date__year=current_year
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        ge_prev = Expense.objects.filter(
            date__year=prev_year,
            date__month__lte=today.month,
            date__day__lte=today.day
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        # Total Expenses Calculation
        exp_cur = ge_cur + payroll_cur
        exp_prev = ge_prev + payroll_prev
        exp_growth = pct_growth(exp_cur, exp_prev)
        exp_diff = exp_cur - exp_prev

        context.update({
            'exp_cur': exp_cur,
            'exp_prev': exp_prev,
            'exp_growth_pct': exp_growth,
            'exp_diff': exp_diff,
            'exp_diff_abs': abs(exp_diff),
        })

        # ─────────────────────────────────────────────
        # Net Profit & Institutional Cost
        # ─────────────────────────────────────────────
        net_cur = rev_cur_net - exp_cur
        net_prev = rev_prev_net - exp_prev

        context.update({
            'net_cur': net_cur,
            'net_prev': net_prev,
            'net_growth_pct': pct_growth(net_cur, net_prev),
            'net_diff': net_cur - net_prev,
            'net_diff_abs': abs(net_cur - net_prev),
            'inst_growth_pct': pct_growth(rev_cur_inst, rev_prev_inst),
            'inst_diff': rev_cur_inst - rev_prev_inst,
            'inst_diff_abs': abs(rev_cur_inst - rev_prev_inst),
            'client_payments_cur': rev_cur_gross,
            'client_payments_prev': rev_prev_gross,
            'client_payments_growth_pct': pct_growth(rev_cur_gross, rev_prev_gross),
            'client_payments_diff': rev_cur_gross - rev_prev_gross,
            'client_payments_diff_abs': abs(rev_cur_gross - rev_prev_gross),
        })

        # ─────────────────────────────────────────────
        # Clients & Titles Stats
        # ─────────────────────────────────────────────
        
        # --- CLIENTS (Stock Metric) ---
        # 1. Current: Total count of all clients ever created
        clients_cur = Client.objects.count()

        # 2. Previous: Total count of clients at the END of last year.
        #    Logic: If we compare against "This day last year" (Jan 4), we exclude 
        #    clients created in late 2025, which makes 2026 look like huge artificial growth.
        #    Comparing against "End of 2025" gives us "New Clients added in 2026".
        clients_prev = Client.objects.filter(
            created_at__year__lte=prev_year
        ).count()


        context.update({
            'clients_cur': clients_cur,
            'clients_prev': clients_prev,
            'clients_growth_pct': pct_growth(Decimal(clients_cur), Decimal(clients_prev)),
            'clients_diff': clients_cur - clients_prev,
            'clients_diff_abs': abs(clients_cur - clients_prev),
        })

        # --- TITLES (Flow Metric: Performance This Year) ---
        # Title collection is a unit of work/revenue, so we keep this as "This Year" vs "Last Year".
        titles_cur = TitleDeedCollection.objects.filter(collected_at__year=current_year).count()
        titles_prev = TitleDeedCollection.objects.filter(
            collected_at__year=prev_year,
            collected_at__month__lte=today.month,
            collected_at__day__lte=today.day
        ).count()

        context.update({
            'titles_cur': titles_cur,
            'titles_prev': titles_prev,
            'titles_growth_pct': pct_growth(Decimal(titles_cur), Decimal(titles_prev)),
            'titles_diff': titles_cur - titles_prev,
            'titles_diff_abs': abs(titles_cur - titles_prev),
        })

        # ─────────────────────────────────────────────
        # Monthly drill-downs (Charts)
        # ─────────────────────────────────────────────
        context['month_labels'] = list(OrderedDict((calendar.month_abbr[m], None) for m in range(1, 13)))
        context['clients_monthly'] = monthly_series(Client, 'created_at', 'id', current_year)
        context['titles_monthly'] = monthly_series(TitleDeedCollection, 'collected_at', 'id', current_year)
        context['revenue_monthly'] = monthly_company_revenue(current_year)

        ss_monthly = monthly_series(
            ClientSubService.objects.annotate(amt=Coalesce('overridden_price', F('sub_service__price'))),
            'added_on', 'amt', current_year
        )
        payroll_monthly = monthly_series(
            Payroll.objects.filter(is_paid=True), 'month', 'net_salary', current_year
        )
        ge_monthly = monthly_series(Expense, 'date', 'amount', current_year)

        # Combine all expenses for the chart
        context['expense_monthly'] = [
            ss_monthly[i] + ge_monthly[i] + payroll_monthly[i] for i in range(12)
        ]

        # Calculate Net Profit per month for the chart
        context['net_monthly'] = [
            context['revenue_monthly'][i] - context['expense_monthly'][i] for i in range(12)
        ]

        # ─────────────────────────────────────────────
        # Daily/Recent Activities
        # ─────────────────────────────────────────────
        context['today_bookings'] = Booking.objects.filter(
            scheduled_date__date=today, handled=False
        )
        context['recent_payments'] = get_all_payment_history()[:10]

        return context  
    


class HomeView(LoginRequiredMixin, DashboardView):
    template_name = 'Home/admin.html'

    def dispatch(self, request, *args, **kwargs):
        # 1) not logged in? → login
        if not request.user.is_authenticated:
            return redirect('login')

        # 2) is superuser or role=Admin? → render
        try:
            if request.user.is_superuser or request.user.employeeprofile.role == EmployeeProfile.RoleChoices.ADMIN:
                return super().dispatch(request, *args, **kwargs)
        except ObjectDoesNotExist:
            pass

        # 3) otherwise → staff
        return redirect('staff-dashboard')


class StaffDashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'Home/staff_dashboard.html'

    def dispatch(self, request, *args, **kwargs):
        try:
            employee_profile = request.user.employeeprofile
            if request.user.is_superuser or employee_profile.role == EmployeeProfile.RoleChoices.ADMIN:
                messages.warning(request, "Admins should access the admin dashboard.")
                return redirect('home')
        except ObjectDoesNotExist:
            messages.error(request, "Employee profile not found. Please contact the administrator.")
            raise PermissionDenied("Missing employee profile.")

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):

        context = super().get_context_data(**kwargs)
        context['total_clients'] = Client.objects.count()
        context['pending_services'] = ClientService.objects.filter(status='pending').count()
        context['in_progress'] = ClientServiceProcess.objects.filter(status='in_progress').count()
        context['completed_today'] = ClientService.objects.filter(
            status='completed',
            updated_at__date=now().date()
        ).count()

        try:
            recent_clients = Client.objects.prefetch_related(
                Prefetch(
                    'client_services',
                    queryset=ClientService.objects.order_by('-requested_at'),
                    to_attr='latest_services'
                )
            ).order_by('-created_at')[:10]

            client_data = []
            for client in recent_clients:
                form = ClientForm(instance=client)
                client_service = client.latest_services[0] if hasattr(client,
                                                                      'latest_services') and client.latest_services else None
                current_process = None

                if client_service:
                    processes = client_service.service_processes.select_related('process') \
                        .order_by('-completed_at', '-id')

                    current_process = (
                            processes.filter(status='in_progress').first()
                            or processes.filter(status='collected').first()
                            or processes.filter(status='completed').first()
                    )

                client_data.append({
                    'client': client,
                    'form': form,
                    'client_service': client_service,
                    'current_process': current_process,
                })

            context['client_data'] = client_data

        except Exception as e:
            messages.error(self.request, "An error occurred while loading dashboard data.")
            context['client_data'] = []

        return context





class ClientDetailView(RolePermissionRequiredMixin, DetailView):
    """
    Displays the details for a single client, including services, subservices,
    payment histories, documents, and forms for actions.
    """
    model = Client
    template_name = 'Client/client_details.html'
    action = 'view'
    context_object_name = 'client'
    pk_url_kwarg = 'client_id'
    permission_required = 'easydocs.view_client'
    raise_exception = True

    def get_context_data(self, **kwargs):
        from types import SimpleNamespace
        context = super().get_context_data(**kwargs)
        client = self.object

        # summary helper
        summary = get_client_service_summary(client)
        context['client_service_summary'] = summary

        def safe_qs(qs_callable, error_msg, fallback):
            try:
                return qs_callable()
            except Exception as e:
                messages.error(self.request, error_msg)
                return fallback

        # subservices
        context['subservices'] = safe_qs(
            lambda: SubService.objects.all(),
            "Could not load subservices.", []
        )

        # payment history
        context['histories'] = safe_qs(
            lambda: PaymentHistory.objects.filter(client_service__client=client).order_by('-created_at'),
            "Could not load payment history.", []
        )
        context['flat_payment_history'] = safe_qs(
            lambda: get_client_payment_history(client_id=client.id),
            "Error generating payment history.", []
        )

        # client subservices
        context['client_subservices'] = safe_qs(
            lambda: ClientSubService.objects.filter(client_service__client=client)
                                            .select_related('sub_service', 'client_service'),
            "Could not load client subservices.", []
        )

        # ------------------ BOOKINGS REFACTOR ------------------
        try:
            services_qs = ClientService.objects.filter(client=client).select_related('service')

            services_qs = services_qs.prefetch_related(
                'payments',
                'sub_services',
                Prefetch(
                    'service_processes',
                    queryset=ClientServiceProcess.objects.select_related('process')
                                                        .order_by('process__step_order')
                ),
                Prefetch(
                    'bookings',
                    queryset=Booking.objects.order_by('-scheduled_date'),
                    to_attr='prefetched_bookings'
                ),
            ).order_by('-requested_at')

            for cs in services_qs:
                # annotate helper props
                cs.latest_process = cs.service_processes.last() if cs.service_processes.exists() else None
                cs.needs_collection = cs.service.requires_title_collection
                try:
                    _ = cs.title_deed_collection
                    cs.has_title_deed_collection = True
                except TitleDeedCollection.DoesNotExist:
                    cs.has_title_deed_collection = False

                # normalize bookings into a flat list of SimpleNamespace objects
                # Ensure ground_data is always a list, even if empty
                prefetched_bookings = getattr(cs, 'prefetched_bookings', [])
                cs.ground_data = [
                    SimpleNamespace(
                        id=b.id,
                        scheduled_date=b.scheduled_date,
                        dispatch_message=b.dispatch_message,
                        created_at=b.created_at,
                        handled=getattr(b, 'handled', False),
                    )
                    for b in prefetched_bookings
                ] if prefetched_bookings else []  # Ensure it's always a list

            context['all_services'] = services_qs
            

            # optional focus on one service
            service_id = self.request.GET.get('service_id')
            if service_id:
                current = next((s for s in services_qs if str(s.id) == str(service_id)), None)
                context['current_service'] = current
                context['service_bookings'] = current.ground_data if current else []
            else:
                context['current_service'] = None
                context['service_bookings'] = []

        except Exception:
            messages.error(self.request, "Could not load client services.")
            context['all_services'] = []
            context['service_bookings'] = []
            context['current_service'] = None
        # ------------------ END BOOKINGS ------------------


        # message logs
        context['message_logs'] = safe_qs(
            lambda: MessageLog.objects.filter(client=client).order_by('-timestamp'),
            "Could not load message logs.", []
        )

        # docs
        try:
            context['doc_types'] = DocType.objects.all()
            context['client_docs'] = ClientDoc.objects.filter(client=client)
        except Exception:
            messages.error(self.request, "Could not load documents.")
            context['doc_types'] = []
            context['client_docs'] = []

        # forms
        context.update({
            'client_service_form': ClientServiceForm(initial={'client': client}),
            'client_subservice_form': ClientSubServiceForm(),
            'title_deed_form': TitleDeedCollectionForm(),
            'doc_form': ClientDocumentForm(),
            'doc_type_form': DocTypeForm(),
            'add_client_form': ClientForm(),
            'client_sms_form': ClientSmsForm(),
        })
        
        # ------------------ SURVEYORS & ASSIGNED IDS ------------------
        try:
            # all available surveyors
            from apps.Employee.models import User
            surveyors = User.objects.filter(
            employeeprofile__role=EmployeeProfile.RoleChoices.SURVEYOR
         )
            context['surveyors'] = surveyors

            # map of booking_id → assigned surveyor ids
            assigned_ids_map = {}
            bookings = Booking.objects.filter(client_service__client=client).prefetch_related('surveyors')
            for booking in bookings:
                assigned_ids_map[booking.id] = list(booking.surveyors.values_list('id', flat=True))

            context['assigned_ids_map'] = assigned_ids_map

        except Exception as e:
            messages.error(self.request, f"Error loading surveyor assignments: {e}")
            context['surveyors'] = []
            context['assigned_ids_map'] = {}

        return context






def client_list(request):
    services = Service.objects.all()
    add_form = ClientForm()
    client_service_form = ClientServiceForm()

    # Prefetch latest services for performance
    clients = Client.objects.prefetch_related(
        Prefetch(
            'client_services',
            queryset=ClientService.objects.order_by('-requested_at'),
            to_attr='latest_services'
        )
    )

    client_data = []
    for client in clients:
        form = ClientForm(instance=client)
        client_service = client.latest_services[0] if hasattr(client, 'latest_services') and client.latest_services else None
        current_process = None

        if client_service:
            processes = client_service.service_processes.select_related('process').order_by('-completed_at', '-id')

            # Pick priority process status
            current_process = (
                processes.filter(status='in_progress').first() or
                processes.filter(status='collected').first() or
                processes.filter(status='completed').first()
            )

        client_data.append({
            'client': client,
            'form': form,
            'client_service': client_service,
            'current_process': current_process,
        })

    context = {
        'client_data': client_data,
        'add_form': add_form,
        'client_service_form': client_service_form,
        'services': services,
    }

    return render(request, 'Client/client_list.html', context)




def add_client(request):
    form = ClientForm(request.POST or None, request.FILES or None)

    # 🟢 AJAX submission (via modal)
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        if form.is_valid():
            client = form.save()
            return JsonResponse({
                "success": True,
                "message": "✅ Client added successfully.",
                "client_id": client.id
            })

        # Return errors in both `errors` and `message`
        errors_dict = {
            field: [{"message": err} for err in errs]
            for field, errs in form.errors.items()
        }

        # Flatten errors into a single message string
        errors_flat = []
        for field, field_errors in errors_dict.items():
            for err in field_errors:
                errors_flat.append(f"{field}: {err['message']}")

        return JsonResponse({
            "success": False,
            "errors": errors_dict,
            "message": "⚠️ " + "; ".join(errors_flat)  # Include actual errors here
        }, status=400)

    # 🟠 Normal non-AJAX fallback
    else:
        if form.is_valid():
            form.save()
            messages.success(request, "✅ Client added successfully.")
            return redirect("clients")

        # Flatten errors for messages framework
        errors_flat = []
        for field, errs in form.errors.items():
            for err in errs:
                errors_flat.append(f"{field}: {err}")

        messages.error(request, "⚠️ " + "; ".join(errors_flat))
        return redirect("clients")



# Edit Client
def edit_client(request, client_id):
    client = get_object_or_404(Client, id=client_id)

    if request.method == 'POST':
        form = ClientForm(request.POST, request.FILES, instance=client)  # include request.FILES
        if form.is_valid():
            try:
                form.save()
                if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                    return JsonResponse({'message': 'Client updated successfully.'})
                messages.success(request, 'Client updated successfully.')
            except Exception as exc:
                logger.exception("Unexpected error when updating client id=%s", client_id)
                user_msg = f"Failed to update client: {str(exc)}"
                if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                    return JsonResponse({'error': 'Failed to update client', 'details': str(exc)}, status=500)
                messages.error(request, user_msg)
        else:
            errors_text = form.errors.as_text()
            errors_json = form.errors.get_json_data()
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'errors': errors_json}, status=400)
            messages.error(request, f"Failed to update client: {errors_text}")

    # preserve referer behavior
    referer = request.META.get('HTTP_REFERER')
    if referer:
        return redirect(referer)
    return redirect(reverse('client_details', kwargs={'client_id': client_id}))



class ClientServiceCreateView(LoginRequiredMixin,CreateView):
    model = ClientService
    form_class = ClientServiceForm

    def form_valid(self, form):
        try:
            with transaction.atomic():
                client_service = form.save(commit=False)

                client_service.scheduled_date = self.request.POST.get('scheduled_date') or None
                client_service.dispatch_message = self.request.POST.get('dispatch_message') or None

                override_total_price = self.request.POST.get('override_total_price')
                if override_total_price:
                    try:
                        client_service.overridden_total_price = Decimal(override_total_price)
                    except InvalidOperation:
                        client_service.overridden_total_price = None
                        messages.warning(self.request, "⚠️ Invalid total price override. Ignored.")

                client_service.save()

                # Delegate to shared helper (is_new=True ensures sync-add happens)
                apply_client_service_logic(
                    cs=client_service,
                    service=client_service.service,
                    post_data=self.request.POST,
                    is_new=True
                )

            messages.success(self.request, "✅ Service assigned successfully.")
            return JsonResponse({'success': True, 'client_service_id': client_service.pk})

        except Exception as e:
            logger.exception("Error creating ClientService: %s", e)
            messages.error(self.request, f"❌ An unexpected error occurred: {str(e)}")
            return JsonResponse({'success': False, 'error': str(e)}, status=500)

    def form_invalid(self, form):
        return JsonResponse({'success': False, 'errors': form.errors}, status=400)




def edit_client_service(request, client_id):
    client = get_object_or_404(Client, id=client_id)

    if request.method == 'POST':
        client_service_id = request.POST.get('client_service_id')
        client_service = get_object_or_404(ClientService, id=client_service_id, client=client)

        try:
            with transaction.atomic():
                new_service = get_object_or_404(Service, id=request.POST.get('service'))
                service_changed = client_service.service_id != new_service.id  # <-- check before overwrite

                client_service.service = new_service
                client_service.land_description = request.POST.get('land_description', '')

                if new_service.category == ServiceCategory.GROUND:
                    client_service.scheduled_date = request.POST.get('scheduled_date') or None
                    client_service.dispatch_message = request.POST.get('dispatch_preview') or None

                client_service.save()

                apply_client_service_logic(
                    cs=client_service,
                    service=new_service,
                    post_data=request.POST,
                    is_new=service_changed  # 👈 treat as "new" if service changed
                )

            return JsonResponse({'success': True, 'message': '✅ Service updated successfully.'})

        except Exception as e:
            logger.exception("Error updating ClientService: %s", e)
            return JsonResponse({'success': False, 'error': f'❌ Failed to update service: {str(e)}'}, status=500)

    return render(request, 'edit_client_service.html', {'client': client})





def search_clients(request):
    term = request.GET.get('term', '').strip()
    qs = Client.objects.all()

    if term:
        qs = qs.filter(
            Q(first_name__icontains=term) |
            Q(last_name__icontains=term) |
            Q(email__icontains=term) |
            Q(phone__icontains=term)
        )

    results = (
        qs
        .values('id', 'first_name', 'last_name', 'phone')
        .order_by('first_name')[:20]
    )

    return JsonResponse({
        'results': list(results),
        'total': qs.count() if term else None,
    })


def get_grouped_services(client):
    services = ClientService.objects.filter(client=client).select_related('service')
    grouped = defaultdict(list)

    for service in services:
        grouped[service.land_description].append(service)

    return grouped


# views.py


# views.py
def update_site_settings(request):
    """
    Handle SiteSettings form submission. Redirect back to Referer.
    """
    # Only allow POST
    if request.method == 'POST':
        try:
            settings_instance = SiteSettings.objects.get(singleton_enforcer=True)
            form = SiteSettingsForm(request.POST, request.FILES, instance=settings_instance)
        except SiteSettings.DoesNotExist:
            # Create new instance
            form = SiteSettingsForm(request.POST, request.FILES)

        if form.is_valid():
            settings = form.save(commit=False)
            # Set singleton_enforcer field to ensure uniqueness
            settings.singleton_enforcer = True
            settings.save()
            messages.success(request, "Site settings saved successfully.")
        else:
            messages.error(request, "Please correct the errors in the form.")

    # Redirect back to the page that submitted the form
    referer = request.META.get('HTTP_REFERER', '/')
    return redirect(referer)


@require_http_methods(["POST"])
def update_sms_token(request):
    instance = SmsProviderToken.objects.get_or_create(singleton_enforcer=True)[0]
    form = SmsProviderTokenForm(request.POST, instance=instance)

    if form.is_valid():
        form.save()
        messages.success(request, "SMS provider token updated successfully.")
    else:
        messages.error(request, "Failed to update token. Please check the input.")

    return redirect(request.META.get('HTTP_REFERER', 'management'))




class ManagementView(LoginRequiredMixin, TemplateView):
    template_name = "Management/management.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from .forms import GoogleDriveConfigForm

        # Data fetching
        context['services'] = Service.objects.prefetch_related('processes').all()
        context['subservices'] = SubService.objects.all()

        # Blank forms
        context.update({
            'service_form': ServiceForm(),
            'process_form': ProcessForm(),
            'subservice_form': SubServiceForm(),
            'edit_service_form': ServiceForm(),
            'edit_process_form': ProcessForm(),
            'edit_subservice_form': SubServiceForm(),
        })

        # --- SiteSettings Safe Fallback ---
        try:
            site_settings = SiteSettings.objects.get(singleton_enforcer=True)
            context['settings_form'] = SiteSettingsForm(instance=site_settings)
        except SiteSettings.DoesNotExist:
            site_settings = SimpleNamespace(
                logo=None,
                company_name='',
                company_email='',
                company_phone='',
                tagline='',
                google_drive_enabled=False,
                google_drive_root_folder_id='',
                google_oauth_client_id='',
                google_drive_service_account_email='',
                google_drive_service_account_key_encrypted=None,
                drive_auto_folder_creation=True,
                drive_file_naming_pattern='{client_id}/{year}/{month}/{filename}',
                stamp_signature=None,
            )
            context['settings_form'] = SiteSettingsForm()
            messages.warning(self.request, "No site settings found. You can create new settings.")

        context['settings'] = site_settings

        # --- Google Drive Form (always safe) ---
        gdrive_initial = {
            'google_drive_enabled': bool(getattr(site_settings, 'google_drive_enabled', False)),
            'google_drive_root_folder_id': getattr(site_settings, 'google_drive_root_folder_id', '') or '',
            'google_oauth_client_id': getattr(site_settings, 'google_oauth_client_id', '') or '',
            'google_oauth_client_secret': '',  # security
            'drive_auto_folder_creation': bool(getattr(site_settings, 'drive_auto_folder_creation', True)),
            'drive_file_naming_pattern': getattr(site_settings, 'drive_file_naming_pattern', '{client_id}/{year}/{month}/{filename}'),
        }
        context['gdrive_form'] = GoogleDriveConfigForm(initial=gdrive_initial)

        # --- Additional context flags ---
        context['site_settings'] = site_settings
        context['has_encrypted_key'] = bool(getattr(site_settings, 'google_drive_service_account_key_encrypted', None))
        context['service_account_email'] = getattr(site_settings, 'google_drive_service_account_email', None)

        # Connection status (safe to call even with dummy)
        from apps.EasyDocs.files.utils import get_connection_status
        context['connection_status'] = get_connection_status(site_settings)

        # --- SMS Token Safe Fallback ---
        try:
            sms_token, _ = SmsProviderToken.objects.get_or_create(singleton_enforcer=True)
            context['sms_token'] = sms_token
            context['sms_token_form'] = SmsProviderTokenForm(instance=sms_token)
        except Exception as e:
            context['sms_token'] = None
            context['sms_token_form'] = SmsProviderTokenForm()
            messages.error(self.request, f"SMS provider token error: {e}")

        # --- Editing Forms ---
        for key, model, form_key, form_class in [
            ('edit_service', Service, 'edit_service_form', ServiceForm),
            ('edit_process', Process, 'edit_process_form', ProcessForm),
            ('edit_subservice', SubService, 'edit_subservice_form', SubServiceForm),
        ]:
            obj_id = self.request.GET.get(key)
            if obj_id:
                try:
                    instance = get_object_or_404(model, id=obj_id)
                    context[form_key] = form_class(instance=instance)
                except Exception as e:
                    messages.error(self.request, f"Error loading {key.replace('edit_', '')}: {e}")

        return context


    def post(self, request, *args, **kwargs):
        handlers = {
            'add_service': self.handle_add_service,
            'edit_service': self.handle_edit_service,
            'add_process': self.handle_add_process,
            'edit_process': self.handle_edit_process,
            'add_subservice': self.handle_add_subservice,
            'edit_subservice': self.handle_edit_subservice,
        }

        for key, handler in handlers.items():
            if key in request.POST:
                return handler(request)

        # fallback - invalid form, re-render
        context = self.get_context_data()
        context.update({
            'service_form': ServiceForm(request.POST),
            'process_form': ProcessForm(request.POST),
            'subservice_form': SubServiceForm(request.POST),
        })
        return self.render_to_response(context)

    # --- Individual Handlers Below ---

    def handle_add_service(self, request):
        form = ServiceForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('management')
        messages.error(request, "Failed to add service.")
        return self.render_invalid_context(form_key='service_form', form=form)

    def handle_edit_service(self, request):
        try:
            service = get_object_or_404(Service, id=request.POST.get('service_id'))
            form = ServiceForm(request.POST, instance=service)
            if form.is_valid():
                form.save()
                return redirect('management')
            # DEBUG: see exactly what failed
            print(form.errors)
            messages.error(request, "Invalid data for editing service.")
        except Exception as e:
            messages.error(request, f"Error editing service: {e}")
        # <-- use edit_service_form, not service_form
        return self.render_invalid_context('edit_service_form', form)

    def handle_add_process(self, request):
        service_id = request.POST.get('service')
        if not (service_id and service_id.isdigit()):
            messages.error(request, "Invalid service selected.")
            return redirect('management')

        try:
            service = get_object_or_404(Service, id=service_id)
            form = ProcessForm(request.POST)
            if form.is_valid():
                process = form.save(commit=False)
                process.service = service
                process.save()
                return redirect('management')
            messages.error(request, "Invalid process form.")
        except Exception as e:
            messages.error(request, f"Error adding process: {e}")
        return self.render_invalid_context('process_form', ProcessForm(request.POST))

    def handle_edit_process(self, request):
        try:
            process = get_object_or_404(Process, id=request.POST.get('process_id'))
            form = ProcessForm(request.POST, instance=process)
            if form.is_valid():
                form.save()
                return redirect('management')
            messages.error(request, "Invalid data for editing process.")
        except Exception as e:
            messages.error(request, f"Error editing process: {e}")
        return self.render_invalid_context('process_form', ProcessForm(request.POST))

    def handle_add_subservice(self, request):
        form = SubServiceForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('management')
        messages.error(request, "Invalid subservice form.")
        return self.render_invalid_context('subservice_form', form)

    def handle_edit_subservice(self, request):
        try:
            subservice = get_object_or_404(SubService, id=request.POST.get('subservice_id'))
            form = SubServiceForm(request.POST, instance=subservice)
            if form.is_valid():
                form.save()
                return redirect('management')
            messages.error(request, "Invalid data for editing subservice.")
        except Exception as e:
            messages.error(request, f"Error editing subservice: {e}")
        return self.render_invalid_context('subservice_form', SubServiceForm(request.POST))

    def render_invalid_context(self, form_key, form):
        context = self.get_context_data()
        context[form_key] = form
        return self.render_to_response(context)