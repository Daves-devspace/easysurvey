from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.timezone import now
from django.views.decorators.http import require_POST

from .forms import TitleDeedCollectionForm
from .models import ClientServiceProcess, ClientService


def is_authorized(user):
    return user.is_authenticated and user.is_staff  # or custom permission check


def safe_complete_process(process_id):
    try:
        process = ClientServiceProcess.objects.get(pk=process_id)
        if process.status != 'completed':
            process.status = 'completed'
            process.completed_at = timezone.now()
            process.save(update_fields=['status', 'completed_at'])
            return True
        return False
    except ClientServiceProcess.DoesNotExist:
        return False


# @login_required
# @user_passes_test(is_authorized)
@require_POST
def mark_process_completed(request, pk):
    process = get_object_or_404(ClientServiceProcess, pk=pk)

    all_processes = process.client_service.service_processes.order_by('process__step_order')
    prev_steps = all_processes.filter(process__step_order__lt=process.process.step_order)

    if prev_steps.exclude(status='completed').exists():
        messages.error(request, "Complete all previous steps before marking this one.")
        return redirect(request.META.get('HTTP_REFERER', '/dashboard/'))

    if process.status == 'completed':
        messages.info(request, 'This step is already marked as completed.')
    else:
        safe_complete_process(process.pk)
        messages.success(request, f"Marked '{process.process.name}' as completed.")

    return redirect(request.META.get('HTTP_REFERER', '/dashboard/'))


# @login_required
# @user_passes_test(is_authorized)

@require_POST
def collect_title_deed(request, service_id):
    service = get_object_or_404(ClientService, pk=service_id)
    referer = request.META.get('HTTP_REFERER', '/dashboard/')

    form = TitleDeedCollectionForm(request.POST)
    if form.is_valid():
        title_deed = form.save(commit=False)
        title_deed.client_service = service
        title_deed.save()

        messages.success(request, "Title deed collection recorded successfully.")
    else:
        messages.error(request, "There was an error submitting the form.")

    return redirect(referer)
