from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

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
    Booking

from django.views.generic import TemplateView, DetailView, CreateView
from django.shortcuts import redirect

from apps.EasyDocs.accounts.accounts import get_client_payment_history, get_all_payment_history
from .analytics import get_yearly_revenue_data, get_available_years
from .clients.client_views import get_client_service_summary
from .models import Service, Process
from .forms import ServiceForm, ProcessForm

from apps.Employee.utils.mixins import RolePermissionRequiredMixin

import logging

from .utils import MobileSasaAPI
from ..Employee.models import EmployeeProfile, Payroll

logger = logging.getLogger(__name__)


# Create your views here.
# utils.py (or wherever your function lives)

def chart_data(request):
    year = int(request.GET.get('year', date.today().year))
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


def pct_growth(current: Decimal, previous: Decimal) -> Decimal:
    """
    Calculate percentage growth, returns 100 if previous is zero.
    """
    if previous and previous > 0:
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


def monthly_series(source, date_field: str, agg_field: str, year: int) -> list[float]:
    """
    Build a 12‑element list (Jan→Dec) of aggregated values:
      - If agg_field == 'id', performs COUNT('id')
      - Otherwise performs SUM(agg_field)
    `source` may be either:
      * A Model class (e.g. Payment)
      * A pre‑filtered/annotated QuerySet
    """
    if isinstance(source, QuerySet):
        qs = source
    else:
        qs = source.objects.all()

    # Rename annotation to avoid conflict with existing 'month' field
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

    data = OrderedDict((calendar.month_abbr[m], 0.0) for m in range(1, 13))
    for entry in month_vals:
        mon = entry['month_trunc'].month
        key = calendar.month_abbr[mon]
        data[key] = float(entry['val'] or 0)

    return list(data.values())



class DashboardView(TemplateView):

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        current_year = today.year
        prev_year = current_year - 1

        # ── REVENUE YTD vs Last Year YTD ───────────────────────────────
        rev_cur = (
                Payment.objects
                .filter(payment_date__year=current_year)
                .aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        )
        rev_prev = (
                Payment.objects
                .filter(
                    payment_date__year=prev_year,
                    payment_date__month__lte=today.month,
                    payment_date__day__lte=today.day
                )
                .aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        )
        rev_growth = pct_growth(rev_cur, rev_prev)
        rev_diff = rev_cur - rev_prev

        context.update({
            'rev_cur': rev_cur,
            'rev_prev': rev_prev,
            'rev_growth_pct': rev_growth,
            'rev_diff': rev_diff,
            'rev_diff_abs': abs(rev_diff),
        })

        # ── EXPENSES YTD vs Last Year YTD ──────────────────────────────
        # Payroll expenses
        payroll_cur = (
                Payroll.objects.filter(month__year=current_year, is_paid=True).aggregate(total=Sum('net_salary'))[
                    'total'] or Decimal('0.00')
        )

        payroll_prev = (
                Payroll.objects.filter(
                    month__year=prev_year,
                    month__month__lte=today.month,
                    month__day__lte=today.day,
                    is_paid=True
                )
                .aggregate(total=Sum('net_salary'))['total'] or Decimal('0.00')
        )

        # Sub‑services
        ss_cur = (
                ClientSubService.objects
                .annotate(amt=Coalesce('overridden_price', F('sub_service__price')))
                .filter(added_on__year=current_year)
                .aggregate(total=Sum('amt'))['total'] or Decimal('0.00')
        )
        ss_prev = (
                ClientSubService.objects
                .annotate(amt=Coalesce('overridden_price', F('sub_service__price')))
                .filter(
                    added_on__year=prev_year,
                    added_on__month__lte=today.month,
                    added_on__day__lte=today.day
                )
                .aggregate(total=Sum('amt'))['total'] or Decimal('0.00')
        )
        # General expenses
        ge_cur = (
                Expense.objects
                .filter(date__year=current_year)
                .aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        )
        ge_prev = (
                Expense.objects
                .filter(
                    date__year=prev_year,
                    date__month__lte=today.month,
                    date__day__lte=today.day
                )
                .aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        )
        exp_cur = ss_cur + ge_cur + payroll_cur
        exp_prev = ss_prev + ge_prev + payroll_prev
        exp_growth = pct_growth(exp_cur, exp_prev)
        exp_diff = exp_cur - exp_prev

        context.update({
            'exp_cur': exp_cur,
            'exp_prev': exp_prev,
            'exp_growth_pct': exp_growth,
            'exp_diff': exp_diff,
            'exp_diff_abs': abs(exp_diff),
        })

        # ── NET REVENUE YTD vs Last Year YTD ───────────────────────────
        net_cur = rev_cur - exp_cur
        net_prev = rev_prev - exp_prev
        net_growth = pct_growth(net_cur, net_prev)
        net_diff = net_cur - net_prev

        context.update({
            'net_cur': net_cur,
            'net_prev': net_prev,
            'net_growth_pct': net_growth,
            'net_diff': net_diff,
            'net_diff_abs': abs(net_diff),
        })

        # ── CLIENTS YTD vs Last Year YTD ──────────────────────────────
        clients_cur = Client.objects.filter(created_at__year=current_year).count()
        clients_prev = Client.objects.filter(
            created_at__year=prev_year,
            created_at__month__lte=today.month,
            created_at__day__lte=today.day
        ).count()
        clients_growth = pct_growth(Decimal(clients_cur), Decimal(clients_prev))
        clients_diff = clients_cur - clients_prev

        context.update({
            'clients_cur': clients_cur,
            'clients_prev': clients_prev,
            'clients_growth_pct': clients_growth,
            'clients_diff': clients_diff,
            'clients_diff_abs': abs(clients_diff),
        })

        # ── TITLE DEEDS YTD vs Last Year YTD ──────────────────────────
        titles_cur = TitleDeedCollection.objects.filter(collected_at__year=current_year).count()
        titles_prev = TitleDeedCollection.objects.filter(
            collected_at__year=prev_year,
            collected_at__month__lte=today.month,
            collected_at__day__lte=today.day
        ).count()
        titles_growth = pct_growth(Decimal(titles_cur), Decimal(titles_prev))
        titles_diff = titles_cur - titles_prev

        context.update({
            'titles_cur': titles_cur,
            'titles_prev': titles_prev,
            'titles_growth_pct': titles_growth,
            'titles_diff': titles_diff,
            'titles_diff_abs': abs(titles_diff),
        })

        # ── Monthly drill‑down series (current year Jan→Dec) ───────────
        context['month_labels'] = list(OrderedDict((calendar.month_abbr[m], None) for m in range(1, 13)))
        context['clients_monthly'] = monthly_series(Client, 'created_at', 'id', current_year)
        context['titles_monthly'] = monthly_series(TitleDeedCollection, 'collected_at', 'id', current_year)
        context['revenue_monthly'] = monthly_series(Payment, 'payment_date', 'amount', current_year)

        ss_monthly = monthly_series(
            ClientSubService.objects.annotate(amt=Coalesce('overridden_price', F('sub_service__price'))),
            'added_on', 'amt', current_year
        )
        payroll_monthly = monthly_series(
            Payroll.objects.filter(is_paid=True), 'month', 'net_salary', current_year
        )

        ge_monthly = monthly_series(Expense, 'date', 'amount', current_year)
        context['expense_monthly'] = [
            ss_monthly[i] + ge_monthly[i] + payroll_monthly[i] for i in range(12)
        ]

        context['net_monthly'] = [
            context['revenue_monthly'][i] - context['expense_monthly'][i] for i in range(12)
        ]

        # ── Today’s unhandled bookings ─────────────────────────────────
        context['today_bookings'] = Booking.objects.filter(
            scheduled_date__date=today,
            handled=False
        )

        # ── Recent payments (for detail list) ─────────────────────────
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
        context = super().get_context_data(**kwargs)
        client = self.object
        summary = get_client_service_summary(client)

        context['client_service_summary'] = summary

        # Fetch subservices
        try:
            context['subservices'] = SubService.objects.all()
        except Exception as e:
            messages.error(self.request, "Could not load subservices.")
            context['subservices'] = []

        # Fetch payment history (raw and flat)
        try:
            context['histories'] = PaymentHistory.objects.filter(
                client_service__client=client
            ).order_by('-created_at')
        except Exception:
            messages.error(self.request, "Could not load payment history.")
            context['histories'] = []

        try:
            context['flat_payment_history'] = get_client_payment_history(client_id=client.id)
        except Exception:
            messages.error(self.request, "Error generating payment history.")
            context['flat_payment_history'] = []

        # Fetch client subservices
        try:
            context['client_subservices'] = ClientSubService.objects.filter(
                client_service__client=client
            ).select_related('sub_service', 'client_service')
        except Exception:
            messages.error(self.request, "Could not load client subservices.")
            context['client_subservices'] = []

        # Fetch and annotate services
        try:
            services_qs = ClientService.objects.filter(client=client)
            services_qs = services_qs.select_related('service')
            services_qs = services_qs.prefetch_related(
                'payments', 'sub_services',
                Prefetch(
                    'service_processes',
                    queryset=ClientServiceProcess.objects.select_related('process')
                    .order_by('process__step_order')
                )
            ).order_by('-requested_at')

            # Annotate latest_process and needs_collection
            for cs in services_qs:
                # existing annotations…
                cs.latest_process = cs.service_processes.last() if cs.service_processes.exists() else None
                cs.needs_collection = cs.service.requires_title_collection
                try:
                    _ = cs.title_deed_collection
                    cs.has_title_deed_collection = True
                except TitleDeedCollection.DoesNotExist:
                    cs.has_title_deed_collection = False

                # --- NEW ground_data dict ---
                try:
                    booking = cs.ground_booking  # may raise DoesNotExist
                    cs.ground_data = {
                        'scheduled_date': booking.scheduled_date,
                        'dispatch_message': booking.dispatch_message,
                    }
                except ObjectDoesNotExist:
                    cs.ground_data = {
                        'scheduled_date': None,
                        'dispatch_message': None,
                    }

            context['all_services'] = services_qs

        except Exception:
            messages.error(self.request, "Could not load client services.")
            context['all_services'] = []

        # Fetch message logs
        try:
            context['message_logs'] = MessageLog.objects.filter(
                client=client
            ).order_by('-timestamp')
        except Exception:
            context['message_logs'] = []

        # Fetch documents
        try:
            context['doc_types'] = DocType.objects.all()
            context['client_docs'] = ClientDoc.objects.filter(client=client)
        except Exception:
            messages.error(self.request, "Could not load documents.")
            context['doc_types'] = []
            context['client_docs'] = []

        # Prepare forms
        context.update({
            'client_service_form': ClientServiceForm(initial={'client': client}),
            'client_subservice_form': ClientSubServiceForm(),
            'title_deed_form': TitleDeedCollectionForm(),
            'doc_form': ClientDocumentForm(),
            'doc_type_form': DocTypeForm(),
            'add_client_form': ClientForm(),
            'client_sms_form': ClientSmsForm(),
        })

        return context


def client_list(request):
    services = Service.objects.all()
    add_form = ClientForm()
    client_service_form = ClientServiceForm()

    # Prefetch latest services
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
        client_service = client.latest_services[0] if hasattr(client,
                                                              'latest_services') and client.latest_services else None
        current_process = None

        if client_service:
            processes = client_service.service_processes.select_related('process') \
                .order_by('-completed_at', '-id')

            # Prefer in_progress
            current_process = processes.filter(status='in_progress').first()
            if not current_process:
                # Prefer collected
                current_process = processes.filter(status='collected').first()
            if not current_process:
                # Then completed
                current_process = processes.filter(status='completed').first()

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
        'services': services
    }

    return render(request, 'Client/client_list.html', context)


# Add Client
def add_client(request):
    if request.method == 'POST':
        form = ClientForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Client added successfully.')
        else:
            messages.error(request, 'Failed to add client. Please check the form.')
    return redirect('clients')


# Edit Client


def edit_client(request, client_id):
    client = get_object_or_404(Client, id=client_id)

    if request.method == 'POST':
        form = ClientForm(request.POST, instance=client)
        if form.is_valid():
            form.save()
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'message': 'Client updated successfully.'})
            messages.success(request, 'Client updated successfully.')
        else:
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'errors': form.errors}, status=400)
            messages.error(request, 'Failed to update client. Please check the form.')

    referer = request.META.get('HTTP_REFERER')
    if referer:
        return redirect(referer)
    return redirect(reverse('client_details', kwargs={'client_id': client_id}))


class ClientServiceCreateView(CreateView):
    model = ClientService
    form_class = ClientServiceForm

    def form_valid(self, form):
        try:
            # Save the ClientService object first
            client_service = form.save(commit=False)

            # Optional scheduled date and dispatch message
            scheduled_date = self.request.POST.get('scheduled_date')
            dispatch_message = self.request.POST.get('dispatch_message')

            if scheduled_date:
                client_service.scheduled_date = scheduled_date
            if dispatch_message:
                client_service.dispatch_message = dispatch_message

            client_service.save()

            # Handle custom process costs
            process_ids = self.request.POST.getlist('process_id[]')
            process_costs = self.request.POST.getlist('process_cost[]')

            if process_ids and process_costs:
                for pid, cost_str in zip(process_ids, process_costs):
                    try:
                        cost = Decimal(cost_str)
                        csp = client_service.service_processes.get(process_id=pid)
                        csp.overridden_cost = cost
                        csp.save(update_fields=['overridden_cost'])
                    except (ClientServiceProcess.DoesNotExist, InvalidOperation):
                        continue
            else:
                # Handle override total price if no processes exist
                override_total_price = self.request.POST.get('override_total_price')
                if override_total_price:
                    try:
                        client_service.overridden_total_price = Decimal(override_total_price)
                        client_service.save(update_fields=['overridden_total_price'])
                    except InvalidOperation:
                        messages.warning(
                            self.request, "⚠️ Invalid total price override. Ignored."
                        )

            messages.success(self.request, "✅ Service assigned successfully.")
            return JsonResponse({'success': True})

        except Exception as e:
            messages.error(self.request, f"❌ An unexpected error occurred: {str(e)}")
            return JsonResponse({'success': False, 'error': str(e)}, status=500)

    def form_invalid(self, form):
        return JsonResponse({
            'success': False,
            'errors': form.errors,
        }, status=400)


def edit_client_service(request, client_id):
    client = get_object_or_404(Client, id=client_id)

    if request.method == 'POST':
        # Assuming ClientService has fields: service, category, cost, etc.
        service = request.POST.get('service')
        category = request.POST.get('category')
        cost = request.POST.get('cost')  # Modify according to your form fields

        # Update the client service or create a new one as needed
        client_service = ClientService.objects.filter(client=client).first()
        if client_service:
            client_service.service = service
            client_service.category = category
            client_service.cost = cost
            client_service.save()

        # After the update, redirect to the client detail page
        return redirect('clientdetail', client_id=client.id)

    # If the request method is GET, simply render the template
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


class ManagementView(TemplateView):
    template_name = "Management/management.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Data fetching with error handling
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

        try:
            site_settings = SiteSettings.objects.get(singleton_enforcer=True)
            context['settings'] = site_settings
            context['settings_form'] = SiteSettingsForm(instance=site_settings)
        except SiteSettings.DoesNotExist:
            messages.warning(self.request, "Site settings not found. You can create new settings.")
            context['settings_form'] = SiteSettingsForm()

        try:
            sms_token, _ = SmsProviderToken.objects.get_or_create(singleton_enforcer=True)
            context['sms_token'] = sms_token
            context['sms_token_form'] = SmsProviderTokenForm(instance=sms_token)
        except Exception as e:
            messages.error(self.request, f"SMS provider token error: {e}")

        # Editing forms based on GET params
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
