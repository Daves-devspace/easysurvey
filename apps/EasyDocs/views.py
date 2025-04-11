from collections import defaultdict
from decimal import Decimal

from django.contrib import messages
from django.db.models import Q, Prefetch
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.template.defaultfilters import first
from django.urls import reverse

from apps.EasyDocs.forms import ClientForm, ClientServiceForm, TitleDeedCollectionForm, ClientDocumentForm, DocTypeForm, \
    SubServiceForm, ClientSubServiceForm
from apps.EasyDocs.models import Client, Service, ClientService, ClientServiceProcess, ClientDoc, DocType, SubService, \
    ClientSubService

from django.views.generic import TemplateView, DetailView
from django.shortcuts import redirect

from .accounts import get_client_payments_grouped_by_service
from .documents import handle_document_upload
from .models import Service, Process
from .forms import ServiceForm, ProcessForm
from .services import add_or_update_client_subservice


# Create your views here.


def home(request):

    return render(request, 'Home/admin.html')






class ClientDetailView(DetailView):
    model = Client
    template_name = 'Client/client_details.html'
    context_object_name = 'client'
    pk_url_kwarg = 'client_id'  # from your URL pattern

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        client = self.get_object()

        subservices=SubService.objects.all()

        # Fetch all ClientSubService entries for this client
        client_subservices = ClientSubService.objects.filter(
            client_service__client=client
        ).select_related('sub_service', 'client_service')

        # Handle document upload
        handle_document_upload(self.request, client)

        # Services with latest process
        all_services = (
            ClientService.objects
            .filter(client=client)
            .select_related('service')
            .prefetch_related(
                Prefetch(
                    'service_processes',
                    queryset=ClientServiceProcess.objects.select_related('process').order_by('process__step_order')
                )
            )
            .order_by('-requested_at')
        )

        for service in all_services:
            service.latest_process = None
            if service.service_processes.exists():
                service.latest_process = max(
                    service.service_processes.all(),
                    key=lambda sp: sp.process.step_order
                )

        # Get filters
        service_id = self.request.GET.get('service')
        start_date = self.request.GET.get('start_date')
        end_date = self.request.GET.get('end_date')

        # Get grouped payments
        grouped_payments = get_client_payments_grouped_by_service(
            client_id=client.id,
            start_date=start_date,
            end_date=end_date,
            service_id=service_id
        )

        # Forms and docs
        context.update({
            'client_subservices': client_subservices,
            'subservices':subservices, #to populated the add sub_service modal with subservice in the db
            'all_services': all_services,
            'title_deed_form': TitleDeedCollectionForm(),
            'doc_form': ClientDocumentForm(),
            'doc_type_form': DocTypeForm(),
            'doc_types': DocType.objects.all(),
            'client_docs': ClientDoc.objects.filter(client=client),
            'grouped_payments': grouped_payments,
            'client_subservice_form': ClientSubServiceForm()  # Add the form to context
        })

        return context



    def render_to_response(self, context, **response_kwargs):
        if self.request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'grouped_payments': context['grouped_payments']})
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
        client_service = client.latest_services[0] if hasattr(client, 'latest_services') and client.latest_services else None
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
            client = form.cleaned_data['client']
            service = form.cleaned_data['service']
            land_description = form.cleaned_data['land_description']

            # 🔒 Check for existing assignment
            existing = ClientService.objects.filter(
                client=client,
                service=service,
                land_description=land_description
            ).first()

            if existing:
                messages.warning(request, '⚠️ This service is already assigned to this client for the specified land.')
                return redirect('clients')

            # ✅ Save new assignment
            client_service = form.save()  # Signal auto-triggers

            # 💰 Handle overridden process costs
            process_ids   = request.POST.getlist('process_id[]')
            process_costs = request.POST.getlist('process_cost[]')

            for pid, cost_str in zip(process_ids, process_costs):
                try:
                    csp = client_service.service_processes.get(process_id=pid)
                    csp.overridden_cost = Decimal(cost_str)
                    csp.save(update_fields=['overridden_cost'])
                except ClientServiceProcess.DoesNotExist:
                    continue  # or log this if needed

            messages.success(request, '✅ Service assigned successfully with custom pricing.')
        else:
            messages.error(request, '❌ Error saving service.')

    return redirect('clients')




def search_clients(request):
    term = request.GET.get('term', '')
    clients = Client.objects.filter(
        Q(first_name__icontains=term) |
        Q(last_name__icontains=term) |
        Q(email__icontains=term) |
        Q(phone__icontains=term)
    )[:20]  # limit results

    client_list = []
    for client in clients:
        client_list.append({
            'id': client.id,
            'first_name': client.first_name,
            'last_name': client.last_name,
            'phone': client.phone
        })

    return JsonResponse({'results': client_list})




def get_grouped_services(client):
    services = ClientService.objects.filter(client=client).select_related('service')
    grouped = defaultdict(list)

    for service in services:
        grouped[service.land_description].append(service)

    return grouped


# views.py


# views.py




class ManagementView(TemplateView):
    template_name = "Management/management.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Services
        context['services'] = Service.objects.prefetch_related('processes').all()

        # SubServices (add this line to include the list of subservices)
        context['subservices'] = SubService.objects.all()  # Fetch all subservices

        # Forms for adding new
        context['service_form'] = ServiceForm()
        context['process_form'] = ProcessForm()
        context['subservice_form'] = SubServiceForm()  # Add SubService form

        # Edit forms (None by default)
        context['edit_service_form'] = ServiceForm()
        context['edit_process_form'] = ProcessForm()
        context['edit_subservice_form'] = SubServiceForm()  # Add edit SubService form

        # Check if there's an edit service, process, or subservice request
        service_id = self.request.GET.get('edit_service')
        process_id = self.request.GET.get('edit_process')
        subservice_id = self.request.GET.get('edit_subservice')  # Get subservice ID for editing

        if service_id:
            service = get_object_or_404(Service, id=service_id)
            context['edit_service_form'] = ServiceForm(instance=service)

        if process_id:
            process = get_object_or_404(Process, id=process_id)
            context['edit_process_form'] = ProcessForm(instance=process)

        if subservice_id:
            subservice = get_object_or_404(SubService, id=subservice_id)
            context['edit_subservice_form'] = SubServiceForm(instance=subservice)

        return context

    def post(self, request, *args, **kwargs):
        if 'add_service' in request.POST:
            service_form = ServiceForm(request.POST)
            if service_form.is_valid():
                service_form.save()
                return redirect('management')

        elif 'edit_service' in request.POST:
            service = get_object_or_404(Service, id=request.POST.get('service_id'))
            service_form = ServiceForm(request.POST, instance=service)
            if service_form.is_valid():
                service_form.save()
                return redirect('management')

        elif 'add_process' in request.POST:
            service_id = request.POST.get('service')
            if service_id and service_id.isdigit():
                service = get_object_or_404(Service, id=service_id)
                process_form = ProcessForm(request.POST)
                if process_form.is_valid():
                    process = process_form.save(commit=False)
                    process.service = service
                    process.save()
                    return redirect('management')
            else:
                messages.error(request, "Invalid service selected for the process.")
                return redirect('management')

        elif 'edit_process' in request.POST:
            process = get_object_or_404(Process, id=request.POST.get('process_id'))
            process_form = ProcessForm(request.POST, instance=process)
            if process_form.is_valid():
                process_form.save()
                return redirect('management')

        elif 'add_subservice' in request.POST:  # Handle adding subservice
            subservice_form = SubServiceForm(request.POST)
            if subservice_form.is_valid():
                subservice_form.save()
                return redirect('management')

        elif 'edit_subservice' in request.POST:  # Handle editing subservice
            subservice = get_object_or_404(SubService, id=request.POST.get('subservice_id'))
            subservice_form = SubServiceForm(request.POST, instance=subservice)
            if subservice_form.is_valid():
                subservice_form.save()
                return redirect('management')

        # If invalid, re-render with errors
        context = self.get_context_data()
        context['service_form'] = ServiceForm(request.POST)
        context['process_form'] = ProcessForm(request.POST)
        context['subservice_form'] = SubServiceForm(request.POST)
        return self.render_to_response(context)




