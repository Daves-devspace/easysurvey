import logging
from typing import Optional

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.EasyDocs.models import (
    ClientService,
    ClientServiceProcess,
    ClientServiceProcessAssignment,
    ClientServiceProcessAssignmentLog,
)

logger = logging.getLogger(__name__)
User = get_user_model()


def _active_steps_queryset(client_service: ClientService):
    return client_service.service_processes.exclude(status__in=("completed", "collected"))


@transaction.atomic
def sync_service_assignment_to_process_assignments(
    client_service: ClientService,
    assigned_employee: Optional[object],
    assigned_by: Optional[object] = None,
    reason: str = "",
):
    """
    Keep process-level assignments synchronized with the current service-level assignee.

    Compatibility behavior:
    - one active process assignment per step for the current service assignee,
    - deactivate other active assignees on that step,
    - when service is unassigned, deactivate active assignments on open steps.
    """
    steps = list(_active_steps_queryset(client_service))

    if not steps:
        return {"created": 0, "updated": 0, "deactivated": 0}

    created = 0
    updated = 0
    deactivated = 0

    acceptance_status = "accepted" if client_service.assignment_status == "accepted" else "pending"

    for step in steps:
        if assigned_employee is None:
            changed = ClientServiceProcessAssignment.objects.filter(
                client_service_process=step,
                is_active=True,
            ).update(is_active=False)
            deactivated += changed
            continue

        deactivated += ClientServiceProcessAssignment.objects.filter(
            client_service_process=step,
            is_active=True,
        ).exclude(assignee=assigned_employee).update(is_active=False)

        assignment, was_created = ClientServiceProcessAssignment.objects.get_or_create(
            client_service_process=step,
            assignee=assigned_employee,
            is_active=True,
            defaults={
                "assigned_by": assigned_by,
                "acceptance_status": acceptance_status,
                "accepted_at": timezone.now() if acceptance_status == "accepted" else None,
            },
        )

        if was_created:
            created += 1
            ClientServiceProcessAssignmentLog.objects.create(
                assignment=assignment,
                action="assigned",
                acted_by=assigned_by,
                reason=reason,
                meta={"source": "service_assignment_sync"},
            )
            continue

        assignment_changed = False

        if assignment.assigned_by_id is None and assigned_by is not None:
            assignment.assigned_by = assigned_by
            assignment_changed = True

        if assignment.acceptance_status != acceptance_status:
            assignment.acceptance_status = acceptance_status
            if acceptance_status == "accepted" and assignment.accepted_at is None:
                assignment.accepted_at = timezone.now()
            assignment_changed = True

        if assignment_changed:
            assignment.save()
            updated += 1

    return {"created": created, "updated": updated, "deactivated": deactivated}


@transaction.atomic
def mark_process_assignments_accepted(client_service: ClientService, user, reason: str = ""):
    assignments = ClientServiceProcessAssignment.objects.select_for_update().filter(
        client_service_process__client_service=client_service,
        assignee=user,
        is_active=True,
    )

    updated = 0
    now_ts = timezone.now()
    for assignment in assignments:
        if assignment.acceptance_status == "accepted":
            continue

        assignment.acceptance_status = "accepted"
        if assignment.accepted_at is None:
            assignment.accepted_at = now_ts
        assignment.save(update_fields=["acceptance_status", "accepted_at", "updated_at"])
        ClientServiceProcessAssignmentLog.objects.create(
            assignment=assignment,
            action="accepted",
            acted_by=user,
            reason=reason,
            meta={"source": "service_accept"},
        )
        updated += 1

    return updated


@transaction.atomic
def mark_process_assignments_declined(client_service: ClientService, user, reason: str = ""):
    assignments = ClientServiceProcessAssignment.objects.select_for_update().filter(
        client_service_process__client_service=client_service,
        assignee=user,
        is_active=True,
    )

    updated = 0
    now_ts = timezone.now()
    for assignment in assignments:
        assignment.acceptance_status = "declined"
        assignment.declined_at = now_ts
        assignment.is_active = False
        assignment.save(update_fields=["acceptance_status", "declined_at", "is_active", "updated_at"])
        ClientServiceProcessAssignmentLog.objects.create(
            assignment=assignment,
            action="declined",
            acted_by=user,
            reason=reason,
            meta={"source": "service_decline"},
        )
        updated += 1

    return updated


@transaction.atomic
def handle_accept_process_assignment(assignment_id: int, user, reason: str = ""):
    try:
        assignment = (
            ClientServiceProcessAssignment.objects
            .select_for_update()
            .select_related('assignee', 'client_service_process__client_service')
            .get(pk=assignment_id)
        )
    except ClientServiceProcessAssignment.DoesNotExist:
        return {"success": False, "message": "Process assignment not found."}

    if assignment.assignee_id != user.id:
        return {"success": False, "message": "This process assignment is not assigned to you."}

    if not assignment.is_active:
        return {"success": False, "message": "This process assignment is no longer active."}

    if assignment.acceptance_status == "accepted":
        return {"success": True, "message": "Process assignment already accepted.", "assignment_id": assignment.id}

    assignment.acceptance_status = "accepted"
    assignment.accepted_at = timezone.now()
    assignment.save(update_fields=["acceptance_status", "accepted_at", "updated_at"])

    ClientServiceProcessAssignmentLog.objects.create(
        assignment=assignment,
        action="accepted",
        acted_by=user,
        reason=reason,
        meta={"source": "process_assignment_accept_endpoint"},
    )

    return {"success": True, "message": "Process assignment accepted.", "assignment_id": assignment.id}


@transaction.atomic
def handle_decline_process_assignment(assignment_id: int, user, reason: str = ""):
    try:
        assignment = (
            ClientServiceProcessAssignment.objects
            .select_for_update()
            .select_related('assignee', 'client_service_process__client_service')
            .get(pk=assignment_id)
        )
    except ClientServiceProcessAssignment.DoesNotExist:
        return {"success": False, "message": "Process assignment not found."}

    if assignment.assignee_id != user.id:
        return {"success": False, "message": "This process assignment is not assigned to you."}

    if not assignment.is_active:
        return {"success": False, "message": "This process assignment is no longer active."}

    assignment.acceptance_status = "declined"
    assignment.declined_at = timezone.now()
    assignment.is_active = False
    assignment.save(update_fields=["acceptance_status", "declined_at", "is_active", "updated_at"])

    ClientServiceProcessAssignmentLog.objects.create(
        assignment=assignment,
        action="declined",
        acted_by=user,
        reason=reason,
        meta={"source": "process_assignment_decline_endpoint"},
    )

    return {"success": True, "message": "Process assignment declined.", "assignment_id": assignment.id}


@transaction.atomic
def handle_complete_process_assignment(assignment_id: int, user, note: str = ""):
    from apps.EasyDocs.services.process_workflow import ProcessWorkflowService

    try:
        assignment = (
            ClientServiceProcessAssignment.objects
            .select_for_update()
            .select_related('assignee', 'client_service_process__client_service', 'client_service_process__process')
            .get(pk=assignment_id)
        )
    except ClientServiceProcessAssignment.DoesNotExist:
        return {"success": False, "message": "Process assignment not found."}

    if assignment.assignee_id != user.id:
        return {"success": False, "message": "This process assignment is not assigned to you."}

    if not assignment.is_active:
        return {"success": False, "message": "This process assignment is no longer active."}

    if assignment.acceptance_status != "accepted":
        return {"success": False, "message": "You must accept this assignment before completing it."}

    step = (
        ClientServiceProcess.objects
        .select_for_update()
        .select_related('client_service', 'process')
        .get(pk=assignment.client_service_process_id)
    )

    if step.status != "in_progress":
        return {"success": False, "message": "This process step is not currently in progress."}

    if assignment.completion_status != "completed":
        assignment.completion_status = "completed"
        assignment.completed_at = timezone.now()
        assignment.completion_note = note or assignment.completion_note
        assignment.save(update_fields=["completion_status", "completed_at", "completion_note", "updated_at"])

        ClientServiceProcessAssignmentLog.objects.create(
            assignment=assignment,
            action="completed",
            acted_by=user,
            reason=note,
            meta={"source": "process_assignment_complete_endpoint"},
        )

    accepted_rows = ClientServiceProcessAssignment.objects.select_for_update().filter(
        client_service_process=step,
        is_active=True,
        acceptance_status="accepted",
    )
    accepted_count = accepted_rows.count()
    completed_count = accepted_rows.filter(completion_status="completed").count()

    if accepted_count == 0:
        return {
            "success": True,
            "message": "No accepted assignees yet; step remains in progress.",
            "step_completed": False,
            "accepted_count": accepted_count,
            "completed_count": completed_count,
            "assignment": assignment,
        }

    if completed_count < accepted_count:
        return {
            "success": True,
            "message": f"Recorded completion ({completed_count}/{accepted_count} accepted assignees done).",
            "step_completed": False,
            "accepted_count": accepted_count,
            "completed_count": completed_count,
            "assignment": assignment,
        }

    workflow = ProcessWorkflowService(step.client_service)
    sms_log = workflow.complete_step(step)

    # Build a detailed SMS note matching the pattern in processes.py
    if not sms_log:
        sms_note = " ⚠️ No SMS was attempted."
    elif sms_log.send_status == "sent":
        sms_note = f" 📤 SMS sent ({sms_log.reason})."
    else:
        sms_note = f" ❌ SMS failed ({sms_log.reason})."

    return {
        "success": True,
        "message": f"✅ '{step.process.name}' completed.{sms_note}",
        "step_completed": True,
        "accepted_count": accepted_count,
        "completed_count": completed_count,
        "sms_log_id": getattr(sms_log, "id", None),
        "assignment": assignment,
    }


@transaction.atomic
def handle_assign_users_to_process_step(
    process_step_id: int,
    user_ids,
    assigned_by: Optional[object],
    reason: str = "",
):
    try:
        step = (
            ClientServiceProcess.objects
            .select_for_update()
            .select_related('client_service')
            .get(pk=process_step_id)
        )
    except ClientServiceProcess.DoesNotExist:
        return {"success": False, "message": "Process step not found."}

    normalized_user_ids = []
    for raw in user_ids:
        try:
            normalized_user_ids.append(int(raw))
        except (TypeError, ValueError):
            continue

    target_user_ids = set(normalized_user_ids)

    active_assignments = ClientServiceProcessAssignment.objects.select_for_update().filter(
        client_service_process=step,
        is_active=True,
    )

    deactivated = active_assignments.exclude(assignee_id__in=target_user_ids).update(is_active=False)

    if not target_user_ids:
        return {
            "success": True,
            "message": "Cleared process-step assignees.",
            "created": 0,
            "updated": 0,
            "deactivated": deactivated,
            "step_id": step.id,
        }

    valid_users = User.objects.filter(id__in=target_user_ids)
    valid_user_ids = set(valid_users.values_list('id', flat=True))

    created = 0
    updated = 0

    for user in valid_users:
        assignment, was_created = ClientServiceProcessAssignment.objects.get_or_create(
            client_service_process=step,
            assignee=user,
            is_active=True,
            defaults={
                "assigned_by": assigned_by,
                "acceptance_status": "pending",
                "completion_status": "pending",
            },
        )

        if was_created:
            created += 1
            ClientServiceProcessAssignmentLog.objects.create(
                assignment=assignment,
                action="assigned",
                acted_by=assigned_by,
                reason=reason,
                meta={"source": "assign_users_to_process_step"},
            )
            continue

        assignment_changed = False
        if assignment.acceptance_status == "declined":
            assignment.acceptance_status = "pending"
            assignment.declined_at = None
            assignment_changed = True

        if assignment.assigned_by_id is None and assigned_by is not None:
            assignment.assigned_by = assigned_by
            assignment_changed = True

        if assignment_changed:
            assignment.save()
            updated += 1

    missing_user_ids = sorted(target_user_ids - valid_user_ids)
    message = "Process assignees updated."
    if missing_user_ids:
        message += f" Skipped invalid user ids: {missing_user_ids}."

    return {
        "success": True,
        "message": message,
        "created": created,
        "updated": updated,
        "deactivated": deactivated,
        "step": step,
    }
