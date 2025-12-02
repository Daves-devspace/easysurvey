from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.timezone import now
from django.views.decorators.http import require_POST

from apps.EasyDocs.forms import TitleDeedCollectionForm
from apps.EasyDocs.models import ClientServiceProcess, ClientService
from apps.EasyDocs.services.process_workflow import ProcessWorkflowService


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
    step = get_object_or_404(ClientServiceProcess, pk=pk)
    cs   = step.client_service
    service = ProcessWorkflowService(cs)

    # guard: all previous steps must be done
    prev = cs.service_processes.filter(
        process__step_order__lt=step.process.step_order
    )
    if prev.exclude(status='completed').exists():
        messages.error(request, "Complete all previous steps first.")
        return redirect(request.META.get('HTTP_REFERER', '/dashboard/'))

    try:
        # 1) Complete the step & get *this* SMS log
        sms_log = service.complete_step(step)

        # 2) Build feedback from *that* log
        if not sms_log:
            sms_note = " ⚠️ No SMS was attempted."
        elif sms_log.send_status == 'sent':
            sms_note = f" 📤 SMS sent ({sms_log.reason})."
        else:
            sms_note = f" ❌ SMS failed ({sms_log.reason})."

        # 3) Push a success message
        messages.success(
            request,
            f"✅ Marked '{step.process.name}' completed.{sms_note}"
        )
    except ValueError as e:
        messages.error(request, str(e))

    return redirect(request.META.get('HTTP_REFERER', '/dashboard/'))


# @login_required
# @user_passes_test(is_authorized)




@require_POST
@login_required
def collect_title_deed(request, service_id):
    service = get_object_or_404(ClientService, pk=service_id)
    referer = request.META.get('HTTP_REFERER', '/dashboard/')

    if hasattr(service, 'title_deed_collection'):
        messages.warning(request, "This title deed has already been collected.")
        return redirect(referer)

    form = TitleDeedCollectionForm(request.POST)
    if form.is_valid():
        title_deed = form.save(commit=False)
        title_deed.client_service = service
        title_deed.submitted_by = request.user
        title_deed.save()
        service.status = 'collected'
        service.save(update_fields=['status'])

        messages.success(request, "Title deed collection recorded successfully.")
    else:
        messages.error(request, "There was an error submitting the form.")

    return redirect(referer)