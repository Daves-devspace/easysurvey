from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import PermissionDenied, ObjectDoesNotExist
from django.db.models import Q, Prefetch, Sum, DecimalField, F
from django.db.models.functions import Coalesce, TruncWeek
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.EasyDocs.forms import ClientForm, ClientServiceForm, TitleDeedCollectionForm, ClientDocumentForm, DocTypeForm, \
    SubServiceForm, ClientSubServiceForm, SiteSettingsForm, SmsProviderTokenForm, \
    ClientSubServiceEditForm, ClientSmsForm
from apps.EasyDocs.models import Client, ClientService, ClientServiceProcess, ClientDoc, DocType, SubService, \
    ClientSubService, SiteSettings, SmsProviderToken, PaymentHistory, Expense, Payment, MessageLog, TitleDeedCollection

from django.views.generic import TemplateView, DetailView, CreateView
from django.shortcuts import redirect

from apps.EasyDocs.accounts.accounts import get_client_payment_history, get_all_payment_history
from .analytics import get_yearly_revenue_data, get_available_years
from .clients.client_views import get_client_service_summary
from .models import Service, Process
from .forms import ServiceForm, ProcessForm

import logging

from .utils import MobileSasaAPI

logger = logging.getLogger(__name__)


# Create your views here.
# utils.py (or wherever your function lives)

def chart_data(request):
    year = int(request.GET.get('year', date.today().year))
    data = get_yearly_revenue_data(year)
    return JsonResponse(data)


def stacked_service_data(request):
    year = int(request.GET.get("year", timezone.now().year))
    chart_data = get_monthly_service_data(year)
    return JsonResponse(chart_data)


def get_years(request):
    years = get_available_years()
    return JsonResponse({'years': years})


from django.db.models import Count
from django.db.models.functions import Coalesce
from decimal import Decimal
from datetime import date

from django.db.models.functions import TruncMonth
from collections import OrderedDict
import calendar
from decimal import Decimal
from django.db.models import Count

from collections import OrderedDict
from datetime import date
import calendar
from decimal import Decimal
from django.db.models import Sum, Count, F
from django.db.models.functions import TruncMonth, Coalesce


def get_dashboard_data():
    today = date.today()
    current_year = today.year
    prev_year = current_year - 1
    current_month = today.month
    prev_month = current_month - 1 if current_month > 1 else 12

    # Revenue
    current_col = Payment.objects.filter(payment_date__year=current_year).aggregate(total=Sum('amount'))[
                      'total'] or Decimal('0.00')
    prev_col = Payment.objects.filter(payment_date__year=prev_year).aggregate(total=Sum('amount'))['total'] or Decimal(
        '0.00')

    # Calculate revenue growth percentage
    revenue_growth = 0
    if prev_col and prev_col > 0:
        revenue_growth = round(((current_col - prev_col) / prev_col) * 100, 2)

    # Expenses
    sub_exp = ClientSubService.objects.filter(added_on__year=current_year).aggregate(
        total=Sum(Coalesce('overridden_price', F('sub_service__price')))
    )['total'] or Decimal('0.00')
    gen_exp = Expense.objects.filter(date__year=current_year).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    total_exp = sub_exp + gen_exp
    net_rev = current_col - total_exp

    # Calculate expense growth percentage
    prev_sub_exp = ClientSubService.objects.filter(added_on__year=prev_year).aggregate(
        total=Sum(Coalesce('overridden_price', F('sub_service__price')))
    )['total'] or Decimal('0.00')
    prev_gen_exp = Expense.objects.filter(date__year=prev_year).aggregate(total=Sum('amount'))['total'] or Decimal(
        '0.00')
    prev_total_exp = prev_sub_exp + prev_gen_exp

    expense_growth = 0
    if prev_total_exp and prev_total_exp > 0:
        expense_growth = round(((total_exp - prev_total_exp) / prev_total_exp) * 100, 2)

    # Clients metrics
    current_month_clients = Client.objects.filter(created_at__year=current_year,
                                                  created_at__month=current_month).count()
    prev_month_clients = Client.objects.filter(created_at__year=current_year if prev_month != 12 else prev_year,
                                               created_at__month=prev_month).count()

    clients_growth = 0
    if prev_month_clients > 0:
        clients_growth = round(((current_month_clients - prev_month_clients) / prev_month_clients) * 100, 2)

    # Title deed metrics
    current_week_titles = TitleDeedCollection.objects.filter(
        collected_at__year=current_year,
        collected_at__week=today.isocalendar()[1]
    ).count()

    prev_week_titles = TitleDeedCollection.objects.filter(
        collected_at__year=current_year,
        collected_at__week=today.isocalendar()[1] - 1
    ).count()

    title_growth = 0
    if prev_week_titles > 0:
        title_growth = round(((current_week_titles - prev_week_titles) / prev_week_titles) * 100, 2)

    # Net revenue growth
    prev_net_rev = prev_col - prev_total_exp
    net_rev_growth = 0
    if prev_net_rev and prev_net_rev > 0:
        net_rev_growth = round(((net_rev - prev_net_rev) / prev_net_rev) * 100, 2)

    # Monthly Clients
    monthly_clients = Client.objects.filter(created_at__year=current_year).annotate(
        month=TruncMonth('created_at')
    ).values('month').annotate(count=Count('id')).order_by('month')

    clients_data = OrderedDict((calendar.month_abbr[m], 0) for m in range(1, 13))
    for entry in monthly_clients:
        clients_data[calendar.month_abbr[entry['month'].month]] = entry['count']

    # Monthly Title Deeds
    monthly_titles = TitleDeedCollection.objects.filter(collected_at__year=current_year).annotate(
        month=TruncMonth('collected_at')
    ).values('month').annotate(count=Count('id')).order_by('month')

    title_deeds_data = OrderedDict((calendar.month_abbr[m], 0) for m in range(1, 13))
    for entry in monthly_titles:
        title_deeds_data[calendar.month_abbr[entry['month'].month]] = entry['count']

    # Monthly Revenue
    monthly_col = Payment.objects.filter(payment_date__year=current_year).annotate(
        month=TruncMonth('payment_date')
    ).values('month').annotate(total=Sum('amount')).order_by('month')


    # Monthly Expenses
    monthly_collected_data = OrderedDict((calendar.month_abbr[m], 0) for m in range(1, 13))
    for entry in monthly_col:
        # The key must remain as is (string)
        month_key = calendar.month_abbr[entry['month'].month]
        # The value can be a float
        monthly_collected_data[month_key] = float(entry['total'])

    # Monthly Expenses
    monthly_expenses = OrderedDict((calendar.month_abbr[m], 0) for m in range(1, 13))
    for m in range(1, 13):
        month_key = calendar.month_abbr[m]
        monthly_expenses[month_key] = float(
            ClientSubService.objects.filter(added_on__year=current_year, added_on__month=m).aggregate(
                total=Sum(Coalesce('overridden_price', F('sub_service__price')))
            )['total'] or 0
        ) + float(
            Expense.objects.filter(date__year=current_year, date__month=m).aggregate(total=Sum('amount'))['total'] or 0
        )
    # Monthly Net Revenue
    monthly_net = OrderedDict()
    for m in calendar.month_abbr[1:]:
        monthly_net[m] = monthly_collected_data[m] - monthly_expenses[m]

    # Current month stats
    current_month_abbr = calendar.month_abbr[current_month]
    current_month_revenue = monthly_collected_data[current_month_abbr]
    current_month_expenses = monthly_expenses[current_month_abbr]
    current_month_net = monthly_net[current_month_abbr]

    # Weekly stats for title deeds
    weekly_titles = TitleDeedCollection.objects.filter(
        collected_at__year=current_year,
        collected_at__month=current_month
    ).annotate(
        week=TruncWeek('collected_at')
    ).values('week').annotate(count=Count('id')).order_by('week')

    # Get this month's days for weekly data
    import datetime
    first_day = datetime.date(current_year, current_month, 1)
    days_in_month = calendar.monthrange(current_year, current_month)[1]
    last_day = datetime.date(current_year, current_month, days_in_month)

    # Get all weeks in the month
    weeks_in_month = []
    current_date = first_day
    while current_date <= last_day:
        week_start = current_date - datetime.timedelta(days=current_date.weekday())
        week_end = week_start + datetime.timedelta(days=6)
        weeks_in_month.append((week_start, week_end))
        current_date = week_end + datetime.timedelta(days=1)

    # Get weekly labels
    weekly_labels = ["Week " + str(i + 1) for i in range(len(weeks_in_month))]

    # Convert OrderedDict values to lists and format as JSON for JavaScript
    import json

    clients_data_list = list(clients_data.values())
    title_deeds_data_list = list(title_deeds_data.values())
    collected_data_list = list(monthly_collected_data.values())
    expenses_data_list = list(monthly_expenses.values())
    net_revenue_data_list = list(monthly_net.values())
    month_labels_list = list(clients_data.keys())

    return {
        'current_year': current_year,
        'previous_year': prev_year,
        'current_year_revenue': current_col,
        'previous_year_revenue': prev_col,
        'revenue_growth': revenue_growth,
        'total_expenses': total_exp,
        'expense_growth': expense_growth,
        'net_revenue': net_rev,
        'net_revenue_growth': net_rev_growth,

        # Client metrics
        'total_clients': Client.objects.count(),
        'current_month_clients': current_month_clients,
        'extra_clients': clients_growth,

        # Title deed metrics
        'total_processed_title_deeds': TitleDeedCollection.objects.filter(collected_at__isnull=False).count(),
        'current_week_titles': current_week_titles,
        'title_growth': title_growth,

        # Current month highlights
        'current_month': current_month_abbr,
        'current_month_revenue': current_month_revenue,
        'current_month_expenses': current_month_expenses,
        'current_month_net': current_month_net,

        # Chart data - convert to JSON strings for use in JavaScript
        'clients_data': json.dumps(clients_data_list),
        'title_deeds_data': json.dumps(title_deeds_data_list),
        'collected_data': json.dumps(collected_data_list),
        'expenses_data': json.dumps(expenses_data_list),
        'net_revenue_data': json.dumps(net_revenue_data_list),
        'month_labels': json.dumps(month_labels_list),

        # Weekly title deeds data
        'weekly_titles_data': json.dumps([entry['count'] for entry in weekly_titles]),
        'weekly_labels': json.dumps(weekly_labels)
    }




def home(request):
    dashboard_data = get_dashboard_data()
    recent_payments = get_all_payment_history()[:10]
    context = {
        'recent_payments': recent_payments,
        'dashboard': dashboard_data,
    }

    return render(request, 'Home/admin.html', context)


class ClientDetailView(PermissionRequiredMixin, DetailView):
    """
    Displays the details for a single client, including services, subservices,
    payment histories, documents, and forms for actions.
    """
    model = Client
    template_name = 'Client/client_details.html'
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


# def add_client_service(request):
#     if request.method == 'POST':
#         form = ClientServiceForm(request.POST)
#
#         if form.is_valid():
#             try:
#                 client = form.cleaned_data['client']
#                 service = form.cleaned_data['service']
#                 land_description = form.cleaned_data['land_description']
#
#                 # Check if this service is already assigned to this client
#                 if ClientService.objects.filter(client=client, service=service,
#                                                 land_description=land_description).exists():
#                     messages.warning(request,
#                                      '⚠️ This service is already assigned to this client for the specified land.')
#                     return redirect('clients')
#
#                 # Save client service record
#                 client_service = form.save()
#
#                 # Handle custom process costs
#                 process_ids = request.POST.getlist('process_id[]')
#                 process_costs = request.POST.getlist('process_cost[]')
#
#                 if process_ids and process_costs:
#                     for pid, cost_str in zip(process_ids, process_costs):
#                         try:
#                             cost = Decimal(cost_str)
#                             csp = client_service.service_processes.get(process_id=pid)
#                             csp.overridden_cost = cost
#                             csp.save(update_fields=['overridden_cost'])
#                         except (ClientServiceProcess.DoesNotExist, InvalidOperation):
#                             continue  # Silently skip invalid or missing data
#
#                 else:
#                     override_total_price = request.POST.get('override_total_price')
#                     if override_total_price:
#                         try:
#                             total_price = Decimal(override_total_price)
#                             client_service.overridden_total_price = total_price
#                             client_service.save(update_fields=['overridden_total_price'])
#                         except InvalidOperation:
#                             messages.warning(request, "⚠️ Total price override value is invalid. It was ignored.")
#
#                 messages.success(request, '✅ Service assigned successfully with custom pricing.')
#
#             except Exception as e:
#                 # Catch-all for unexpected errors
#                 messages.error(request, f'❌ An unexpected error occurred: {str(e)}')
#                 return redirect('clients')
#
#         else:
#             messages.error(request, '❌ Form is invalid. Please check the inputs.')
#
#     return redirect('clients')


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
