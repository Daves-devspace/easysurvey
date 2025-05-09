from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q, Prefetch, Sum, DecimalField, F
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.EasyDocs.forms import ClientForm, ClientServiceForm, TitleDeedCollectionForm, ClientDocumentForm, DocTypeForm, \
    SubServiceForm, ClientSubServiceForm, SiteSettingsForm, SmsProviderTokenForm, \
    ClientSubServiceEditForm, ClientSmsForm
from apps.EasyDocs.models import Client, ClientService, ClientServiceProcess, ClientDoc, DocType, SubService, \
    ClientSubService, SiteSettings, SmsProviderToken,  PaymentHistory, Expense, Payment, MessageLog

from django.views.generic import TemplateView, DetailView
from django.shortcuts import redirect

from apps.EasyDocs.accounts.accounts import get_client_payment_history, get_all_payment_history
from .analytics import get_yearly_revenue_data, get_available_years
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


def get_dashboard_data():
    today = date.today()
    current_year = today.year
    prev_year = current_year - 1

    # ————————— Collections —————————
    current_col = Payment.objects.filter(
        payment_date__year=current_year
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    prev_col = Payment.objects.filter(
        payment_date__year=prev_year
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    # ————————— Expenses —————————
    # sub‑services added this year
    sub_exp = ClientSubService.objects.filter(
        added_on__year=current_year
    ).aggregate(
        total=Sum(
            Coalesce('overridden_price', F('sub_service__price')),
            output_field=DecimalField()
        )
    )['total'] or Decimal('0.00')

    # general expenses this year
    gen_exp = Expense.objects.filter(
        date__year=current_year
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    total_exp = sub_exp + gen_exp

    # ————————— Net Revenue —————————
    net_rev = current_col - total_exp

    # ————————— Trend & Extra —————————
    extra = current_col - prev_col
    pct = (extra / prev_col * 100) if prev_col else Decimal('100.00')

    if extra < 0:
        extra_class = 'text-danger'
        badge_class = 'bg-light-danger border border-danger'
        badge_icon = 'ti ti-trending-down'
    else:
        extra_class = 'text-success'
        badge_class = 'bg-light-success border border-success'
        badge_icon = 'ti ti-trending-up'

    return {
        'current_year_revenue': current_col,
        'previous_year_revenue': prev_col,
        'current_year': current_year,
        'previous_year': prev_year,
        'total_expenses': total_exp,
        'net_revenue': net_rev,
        'extra_amount': abs(extra),
        'extra_class': extra_class,
        'trending_badge': {
            'class': badge_class,
            'icon': badge_icon,
            'percentage': f"{pct:.2f}%"
        }
    }


def home(request):
    dashboard_data = get_dashboard_data()
    recent_payments = get_all_payment_history()[:10]
    context = {
        'recent_payments': recent_payments,
        'dashboard': dashboard_data,
    }

    return render(request, 'Home/admin.html', context )



class ClientDetailView(PermissionRequiredMixin, DetailView):
    model = Client
    template_name = 'Client/client_details.html'
    context_object_name = 'client'
    pk_url_kwarg = 'client_id'
    permission_required = 'easydocs.view_client'  # Make sure app_label is correct
    raise_exception = True  # Optional: raises 403 instead of redirecting to login

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        client = self.object

        # — Subservices —
        try:
            subservices = SubService.objects.all()
        except Exception as e:
            logger.warning(f"SubService fetch error: {e}")
            subservices = []
            messages.error(self.request, "Could not load subservices.")

        # — Raw history (for debug or timeline if you need it) —
        try:
            histories = (
                PaymentHistory.objects
                .filter(client_service__client=client)
                .order_by('-created_at')
            )
        except Exception as e:
            logger.error(f"PaymentHistory fetch error: {e}")
            histories = []
            messages.error(self.request, "Could not load payment history.")

        # — Client-subservices —
        try:
            client_subservices = (
                ClientSubService.objects
                .filter(client_service__client=client)
                .select_related('sub_service', 'client_service')
            )
        except Exception as e:
            logger.warning(f"ClientSubService fetch error: {e}")
            client_subservices = []
            messages.error(self.request, "Could not load client subservices.")

        # — All client services, with latest process annotated —
        try:
            all_services = (
                ClientService.objects
                .filter(client=client)
                .select_related('service')
                .prefetch_related(
                    Prefetch(
                        'service_processes',
                        queryset=ClientServiceProcess.objects
                        .select_related('process')
                        .order_by('process__step_order')
                    ),
                    # optional: prefetch payments if you’re displaying paid/balance
                    Prefetch('payments'),
                    # optional: prefetch sub‑services
                    Prefetch('sub_services'),
                )
                .order_by('-requested_at')
            )

            for cs in all_services:
                # 1️⃣ which step is the “last” one?
                cs.latest_process = (
                    cs.service_processes.last()
                    if cs.service_processes.exists()
                    else None
                )

                # 2️⃣ does this service need a title‑deed collection flow?
                cs.needs_collection = cs.service.requires_title_collection

            context['all_services'] = all_services

        except Exception as e:
            logger.error(f"ClientService fetch error: {e}", exc_info=True)
            context['all_services'] = []
            messages.error(self.request, "Could not load client services.")

        # — Flat (aggregated) payment history — no filters applied here any more
        try:
            flat_history = get_client_payment_history(client_id=client.id)
        except Exception as e:
            logger.error(f"Payment history utility error: {e}", exc_info=True)
            flat_history = []
            messages.error(self.request, "Error generating payment history.")

        try:
            logs = MessageLog.objects.filter(client=client).order_by('-timestamp')
        except MessageLog.DoesNotExist:
            logs = []

        # — Documents —
        try:
            doc_types = DocType.objects.all()
            client_docs = ClientDoc.objects.filter(client=client)
        except Exception as e:
            logger.warning(f"Document fetch error: {e}")
            doc_types = []
            client_docs = []
            messages.error(self.request, "Could not load documents.")

        # — Prepare add/edit ClientServiceForm —
        edit_id = self.request.GET.get('edit_service')
        cs_instance = None
        if edit_id:
            try:
                cs_instance = ClientService.objects.get(id=edit_id, client=client)
            except ClientService.DoesNotExist:
                messages.warning(self.request, "Service to edit not found.")

        try:
            client_service_form = ClientServiceForm(
                instance=cs_instance,
                initial={'client': client} if not cs_instance else None
            )
        except Exception as e:
            logger.error(f"ClientServiceForm init error: {e}", exc_info=True)
            client_service_form = ClientServiceForm(initial={'client': client})
            messages.error(self.request, "Could not load service form.")

        # — Inject everything into context —
        context.update({
            'message_logs':logs,
            'subservices': subservices,
            'histories': histories,
            'client_subservices': client_subservices,
            'all_services': all_services,
            'flat_payment_history': flat_history,
            'doc_types': doc_types,
            'client_docs': client_docs,
            'client_service_form': client_service_form,
            'editing_service': cs_instance,
            'client_subservice_form': ClientSubServiceForm(),
            'title_deed_form': TitleDeedCollectionForm(),
            'doc_form': ClientDocumentForm(),
            'doc_type_form': DocTypeForm(),
            'client_sms_form': ClientSmsForm(),
        })
        return context

    def post(self, request, *args, **kwargs):
        # Make sure self.object is set
        self.object = self.get_object()
        client = self.object

        handlers = {
            'send_sms': self.handle_send_client_sms,
            'add_client_service': self.handle_add_client_service,
            'edit_client_service': self.handle_edit_client_service,
            'delete_client_service': self.handle_delete_client_service,
            'add_client_subservice': self.handle_add_client_subservice,  # ←
            'edit_client_subservice': self.handle_edit_client_subservice,  # ←
            'delete_client_subservice': self.handle_delete_client_subservice,  # ←
        }
        for key, handler in handlers.items():
            if key in request.POST:
                return handler(request, client)

        # Fallback: just reload
        return redirect('client_details', client_id=client.id)

    def handle_send_client_sms(self, request, client):
        if not request.user.has_perm('easydocs.send_client_sms'):
            raise PermissionDenied("You don't have permission to send SMS messages.")
        form = ClientSmsForm(request.POST)
        if not form.is_valid():
            messages.error(request, "❌ Please enter a valid message.")
            return self.render_invalid_context('client_sms_form', form)

        text = form.cleaned_data['message']
        api = MobileSasaAPI()
        resp = api.send_sms(client.phone, text)

        # Determine send status
        sent = bool(resp.get('status'))
        status = 'sent' if sent else 'failed'
        delivery = 'pending' if sent else 'failed'
        error = '' if sent else resp.get('message', 'Unknown error')

        # Log it (reason left blank)
        MessageLog.objects.create(
            client=client,
            phone=client.phone,
            message=text,
            reason='',
            send_status=status,
            delivery_status=delivery,
            error_details=error
        )

        # User feedback
        if sent:
            messages.success(request, "✅ SMS sent successfully.")
        else:
            messages.error(request, f"❌ SMS failed: {error}")

        return redirect('client_details', client_id=client.id)

    def handle_add_client_service(self, request, client):
        if not request.user.has_perm('easydocs.add_clientservice'):
            raise PermissionDenied("You don't have permission to add client services.")

        form = ClientServiceForm(request.POST)
        if form.is_valid():
            # Extract key fields to check for duplicates
            service = form.cleaned_data.get("service")
            land_description = form.cleaned_data.get("land_description").strip()

            # Duplicate check
            exists = ClientService.objects.filter(
                client=client,
                service=service,
                land_description__iexact=land_description  # case-insensitive match
            ).exists()

            if exists:
                messages.warning(
                    request,
                    f"⚠️ This service for '{land_description}' already exists for the client."
                )
                return self.render_invalid_context('client_service_form', form)

            # Save the ClientService
            cs = form.save(commit=False)
            cs.client = client
            cs.save()
            form.save_m2m()

            # ⬇️ Override per-process costs if any
            pids = request.POST.getlist('process_id[]')
            costs = request.POST.getlist('process_cost[]')
            if pids and costs:
                for pid, cost_str in zip(pids, costs):
                    try:
                        cost = Decimal(cost_str)
                        csp = cs.service_processes.get(process_id=pid)
                        csp.overridden_cost = cost
                        csp.save(update_fields=['overridden_cost'])
                    except (ClientServiceProcess.DoesNotExist, InvalidOperation):
                        continue
                # Clear total price override if any
                if cs.overridden_total_price is not None:
                    cs.overridden_total_price = None
                    cs.save(update_fields=['overridden_total_price'])
            else:
                # ⬇️ Override total price if given (no processes)
                otp = request.POST.get('override_total_price')
                if otp:
                    try:
                        cs.overridden_total_price = Decimal(otp)
                        cs.save(update_fields=['overridden_total_price'])
                    except InvalidOperation:
                        messages.warning(request, "⚠️ Invalid total price value—ignored.")
                else:
                    # Clear any previous override
                    if cs.overridden_total_price is not None:
                        cs.overridden_total_price = None
                        cs.save(update_fields=['overridden_total_price'])

            # SMS Feedback
            sms_log = cs.message_logs.order_by('-timestamp').first()
            sms_note = ""
            if sms_log:
                if sms_log.send_status == 'sent':
                    sms_note = f" 📤 SMS sent ({sms_log.reason})."
                else:
                    sms_note = f" ❌ SMS failed ({sms_log.reason}): {sms_log.error_details or 'no details'}."
            else:
                sms_note = " ⚠️ No SMS was attempted."

            messages.success(
                request,
                f"✅ Service assigned successfully.{sms_note}"
            )
            return redirect('client_details', client_id=client.id)

        messages.error(request, "❌ Error assigning service.")
        return self.render_invalid_context('client_service_form', form)

    def handle_edit_client_service(self, request, client):
        if not request.user.has_perm('easydocs.change_clientservice'):
            raise PermissionDenied("You don't have permission to edit client services.")

        cs_id = request.POST.get('client_service_id')
        cs = get_object_or_404(ClientService, id=cs_id, client=client)
        form = ClientServiceForm(request.POST, instance=cs)

        if form.is_valid():
            form.save()

            # 1️⃣ Override per‐process costs if any
            pids = request.POST.getlist('process_id[]')
            costs = request.POST.getlist('process_cost[]')
            if pids and costs:
                for pid, cost_str in zip(pids, costs):
                    try:
                        cost = Decimal(cost_str)
                        csp = cs.service_processes.get(process_id=pid)
                        csp.overridden_cost = cost
                        csp.save(update_fields=['overridden_cost'])
                    except (ClientServiceProcess.DoesNotExist, InvalidOperation):
                        continue
                # Clear any previous total override
                if cs.overridden_total_price is not None:
                    cs.overridden_total_price = None
                    cs.save(update_fields=['overridden_total_price'])
            else:
                # 2️⃣ No processes? handle total override
                otp = request.POST.get('override_total_price')
                if otp:
                    try:
                        cs.overridden_total_price = Decimal(otp)
                        cs.save(update_fields=['overridden_total_price'])
                    except InvalidOperation:
                        messages.warning(request, "⚠️ Invalid total price value—ignored.")
                else:
                    # If neither processes nor override given, clear override
                    if cs.overridden_total_price is not None:
                        cs.overridden_total_price = None
                        cs.save(update_fields=['overridden_total_price'])

            messages.success(request, "✅ Service updated successfully.")
            return redirect('client_details', client_id=client.id)

        messages.error(request, "❌ Error updating service.")
        return self.render_invalid_context('client_service_form', form)


    def handle_delete_client_service(self, request, client):
        if not request.user.has_perm('easydocs.delete_clientservice'):
            raise PermissionDenied("You don't have permission to delete client services.")

        cs_id = request.POST.get('client_service_id')
        try:
            cs = ClientService.objects.get(id=cs_id, client=client)
            cs.delete()
            messages.success(request, "🗑️ Client service deleted.")
        except ClientService.DoesNotExist:
            messages.error(request, "⚠️ Client service not found.")
        return redirect('client_details', client_id=client.id)

    def handle_add_client_subservice(self, request, client):
        def handle_add_client_subservice(self, request, client):
            if not request.user.has_perm('easydocs.add_clientsubservice'):
                raise PermissionDenied("You don't have permission to add subservices.")
        # form includes fields: sub_service, overridden_price
        form = ClientSubServiceForm(request.POST)
        if form.is_valid():
            css = form.save(commit=False)
            # tie to the correct ClientService
            css.client_service = get_object_or_404(
                ClientService,
                id=request.POST.get('client_service'),
                client=client
            )
            # cleaned_data already has overridden_price or None
            css.overridden_price = form.cleaned_data.get('overridden_price')
            css.save()
            messages.success(request, "✅ SubService added successfully.")
            return redirect('client_details', client_id=client.id)

        messages.error(request, "❌ Error adding subservice.")
        return self.render_invalid_context('client_subservice_form', form)

    def handle_edit_client_subservice(self, request, client):
        if not request.user.has_perm('easydocs.change_clientsubservice'):
            raise PermissionDenied("You don't have permission to edit subservices.")
        css_id = request.POST.get('client_subservice_id')
        css = get_object_or_404(ClientSubService, id=css_id, client_service__client=client)

        form = ClientSubServiceEditForm(request.POST, instance=css)
        if form.is_valid():
            form.save()
            messages.success(request, "✅ SubService updated successfully.")
            return redirect('client_details', client_id=client.id)

        messages.error(request, "❌ Error updating subservice.")
        return self.render_invalid_context('client_subservice_form', form)

    @staticmethod
    def handle_delete_client_subservice(request, client):
        if not request.user.has_perm('easydocs.delete_clientsubservice'):
            raise PermissionDenied("You don't have permission to delete subservices.")
        css_id = request.POST.get('client_subservice_id')
        try:
            css = ClientSubService.objects.get(
                id=css_id,
                client_service__client=client
            )
            css.delete()
            messages.success(request, "🗑️ SubService deleted.")
        except ClientSubService.DoesNotExist:
            messages.error(request, "⚠️ SubService not found.")
        return redirect('client_details', client_id=client.id)

    def render_invalid_context(self, form_key, form):
        context = self.get_context_data()
        context[form_key] = form
        return self.render_to_response(context)

    def render_to_response(self, context, **response_kwargs):
        # AJAX payment history case
        if self.request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'payment_history': context.get('flat_payment_history', [])})
        return super().render_to_response(context, **response_kwargs)


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
            messages.success(request, 'Client updated successfully.')
        else:
            messages.error(request, 'Failed to update client. Please check the form.')
    return redirect('clients')


def add_client_service(request):
    if request.method == 'POST':
        form = ClientServiceForm(request.POST)

        if form.is_valid():
            try:
                client = form.cleaned_data['client']
                service = form.cleaned_data['service']
                land_description = form.cleaned_data['land_description']

                # Check if this service is already assigned to this client
                if ClientService.objects.filter(client=client, service=service,
                                                land_description=land_description).exists():
                    messages.warning(request,
                                     '⚠️ This service is already assigned to this client for the specified land.')
                    return redirect('clients')

                # Save client service record
                client_service = form.save()

                # Handle custom process costs
                process_ids = request.POST.getlist('process_id[]')
                process_costs = request.POST.getlist('process_cost[]')

                if process_ids and process_costs:
                    for pid, cost_str in zip(process_ids, process_costs):
                        try:
                            cost = Decimal(cost_str)
                            csp = client_service.service_processes.get(process_id=pid)
                            csp.overridden_cost = cost
                            csp.save(update_fields=['overridden_cost'])
                        except (ClientServiceProcess.DoesNotExist, InvalidOperation):
                            continue  # Silently skip invalid or missing data

                else:
                    override_total_price = request.POST.get('override_total_price')
                    if override_total_price:
                        try:
                            total_price = Decimal(override_total_price)
                            client_service.overridden_total_price = total_price
                            client_service.save(update_fields=['overridden_total_price'])
                        except InvalidOperation:
                            messages.warning(request, "⚠️ Total price override value is invalid. It was ignored.")

                messages.success(request, '✅ Service assigned successfully with custom pricing.')

            except Exception as e:
                # Catch-all for unexpected errors
                messages.error(request, f'❌ An unexpected error occurred: {str(e)}')
                return redirect('clients')

        else:
            messages.error(request, '❌ Form is invalid. Please check the inputs.')

    return redirect('clients')


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
