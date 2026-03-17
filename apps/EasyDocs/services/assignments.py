"""
apps/EasyDocs/services/assignments.py

Service assignment acceptance/decline logic.

Responsibilities:
- handle_accept_service: employee accepts a service assignment
- handle_decline_service: employee declines a service assignment  
- notify_reassignment_candidates: notify users about reassignment opportunities
- get_pending_assignments_for_user: fetch all services pending acceptance by user

Design notes:
- Assignment logs are created on accept/decline actions
- Decline transitions to 'unassigned' per requirements, triggers notification to assigner
- On decline, if there are pending reminders assigned to decliner, transfer to assigner or admin-manager
- Returns dict with success/error for caller flexibility
"""

import logging
from typing import Dict, List, Optional
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.EasyDocs.models import (
    ClientService,
    ServiceAssignmentLog,
)
from apps.EasyDocs.services.process_assignments import (
    mark_process_assignments_accepted,
    mark_process_assignments_declined,
    sync_service_assignment_to_process_assignments,
)
from apps.notifications.models import Notification
from apps.notifications.utils import send_push_to_user

logger = logging.getLogger(__name__)
User = get_user_model()


def handle_accept_service(
    client_service_id: int,
    user,
    reason: Optional[str] = None
) -> Dict[str, any]:
    """
    Employee accepts a service assignment.
    
    Args:
        client_service_id: ClientService PK
        user: accepting User instance
        reason: optional acceptance reason/note
        
    Returns:
        dict with keys: success (bool), message (str), client_service (optional)
    """
    try:
        cs = ClientService.objects.select_related(
            'assigned_employee', 'service', 'client'
        ).get(pk=client_service_id)
    except ClientService.DoesNotExist:
        logger.warning(f"Accept failed: ClientService {client_service_id} not found")
        return {"success": False, "message": "Service assignment not found."}
    
    # Verify assignment
    if cs.assigned_employee != user:
        logger.warning(
            f"Accept failed: user {user.id} is not assigned to ClientService {cs.id}"
        )
        return {"success": False, "message": "This service is not assigned to you."}
    
    # Verify current status allows acceptance
    if cs.assignment_status not in ('pending_acceptance', 'reassigned'):
        logger.info(
            f"Accept failed: ClientService {cs.id} is {cs.assignment_status}, not pending_acceptance or reassigned"
        )
        return {
            "success": False,
            "message": f"Cannot accept: status is {cs.get_assignment_status_display()}."
        }
    
    with transaction.atomic():
        # Update status to accepted
        cs.assignment_status = 'accepted'
        cs.save(update_fields=['assignment_status'])
        
        # Log acceptance
        ServiceAssignmentLog.objects.create(
            client_service=cs,
            assigned_employee=user,
            action='accepted',
            assigned_by=None,  # self-action
            reason=reason or "Accepted by employee",
        )

        # Dual-write compatibility: keep process-level assignment rows synchronized.
        try:
            sync_service_assignment_to_process_assignments(
                client_service=cs,
                assigned_employee=user,
                assigned_by=None,
                reason=reason or "Accepted by employee",
            )
            mark_process_assignments_accepted(cs, user, reason=reason or "Accepted by employee")
        except Exception as exc:
            logger.exception(
                "Failed to sync process assignments on acceptance for ClientService %s: %s",
                cs.id,
                exc,
            )

        # Schedule reminders if deadline is set
        try:
            from apps.EasyDocs.services.reminders import schedule_service_reminders
            reminders_created = schedule_service_reminders(cs, user)
            logger.info(
                f"Scheduled {len(reminders_created)} reminders for ClientService {cs.id} "
                f"after acceptance by user {user.id}"
            )
        except Exception as exc:
            logger.exception(
                f"Failed to schedule reminders for ClientService {cs.id} after acceptance: {exc}"
            )
            # Don't fail the acceptance if reminder scheduling fails
        
        # Notify assigner (whoever made the last assignment log entry)
        last_assignment = (
            ServiceAssignmentLog.objects
            .filter(client_service=cs, action__in=('assigned', 'reassigned'))
            .exclude(assigned_by__isnull=True)
            .order_by('-timestamp')
            .first()
        )
        
        if last_assignment and last_assignment.assigned_by:
            assigner = last_assignment.assigned_by
            notify_title = "Service Accepted"
            notify_body = (
                f"{user.get_full_name() or user.username} accepted "
                f"{cs.service.name} for {cs.client.first_name} {cs.client.last_name}."
            )
            
            try:
                Notification.objects.create(
                    user=assigner,
                    title=notify_title,
                    message=notify_body,
                )
                send_push_to_user(assigner, notify_title, notify_body)
            except Exception as exc:
                logger.exception(f"Failed to notify assigner {assigner.id}: {exc}")
        
        logger.info(f"ClientService {cs.id} accepted by user {user.id}")
        return {
            "success": True,
            "message": "Service accepted successfully.",
            "client_service_id": cs.id,
            "assignment_status": cs.assignment_status,
        }
    

def handle_decline_service(
    client_service_id: int,
    user,
    reason: Optional[str] = None
) -> Dict[str, any]:
    """
    Employee declines a service assignment.
    
    Per requirements:
    - status transitions to 'unassigned'
    - assigned_employee is cleared
    - notify assigner + admin-manager roles
    - transfer any pending reminders from decliner to assigner or admin-manager
    
    Args:
        client_service_id: ClientService PK
        user: declining User instance
        reason: optional decline reason
        
    Returns:
        dict with keys: success (bool), message (str), client_service (optional)
    """
    try:
        cs = ClientService.objects.select_related(
            'assigned_employee', 'service', 'client'
        ).get(pk=client_service_id)
    except ClientService.DoesNotExist:
        logger.warning(f"Decline failed: ClientService {client_service_id} not found")
        return {"success": False, "message": "Service assignment not found."}
    
    # Verify assignment
    if cs.assigned_employee != user:
        logger.warning(
            f"Decline failed: user {user.id} is not assigned to ClientService {cs.id}"
        )
        return {"success": False, "message": "This service is not assigned to you."}
    
    # Verify current status allows decline
    if cs.assignment_status not in ('pending_acceptance', 'reassigned', 'accepted'):
        logger.info(
            f"Decline failed: ClientService {cs.id} is {cs.assignment_status}, cannot decline"
        )
        return {
            "success": False,
            "message": f"Cannot decline: status is {cs.get_assignment_status_display()}."
        }
    
    with transaction.atomic():
        # Store previous employee before clearing
        previous_employee = cs.assigned_employee
        
        # Update to unassigned and clear employee
        cs.assigned_employee = None
        cs.assignment_status = 'unassigned'
        cs.save(update_fields=['assigned_employee', 'assignment_status'])
        
        # Log decline
        ServiceAssignmentLog.objects.create(
            client_service=cs,
            assigned_employee=None,
            previous_employee=previous_employee,
            action='declined',
            assigned_by=None,  # self-action
            reason=reason or "Declined by employee",
        )

        # Dual-write compatibility: mark existing process rows declined and deactivate
        try:
            mark_process_assignments_declined(cs, user, reason=reason or "Declined by employee")
            sync_service_assignment_to_process_assignments(
                client_service=cs,
                assigned_employee=None,
                assigned_by=None,
                reason=reason or "Declined by employee",
            )
        except Exception as exc:
            logger.exception(
                "Failed to sync process assignments on decline for ClientService %s: %s",
                cs.id,
                exc,
            )

        # Cancel pending reminders
        try:
            from apps.EasyDocs.services.reminders import cancel_service_reminders
            cancelled_count = cancel_service_reminders(
                cs,
                reason=f"Service declined by {user.get_full_name() or user.username}"
            )
            logger.info(
                f"Cancelled {cancelled_count} reminders for ClientService {cs.id} "
                f"after decline by user {user.id}"
            )
        except Exception as exc:
            logger.exception(
                f"Failed to cancel reminders for ClientService {cs.id} after decline: {exc}"
            )
            # Don't fail the decline if reminder cancellation fails
        
        # Find assigner for notification
        last_assignment = (
            ServiceAssignmentLog.objects
            .filter(client_service=cs, action__in=('assigned', 'reassigned'))
            .exclude(assigned_by__isnull=True)
            .order_by('-timestamp')
            .first()
        )
        
        notify_users = []
        if last_assignment and last_assignment.assigned_by:
            notify_users.append(last_assignment.assigned_by)
        
        # Also notify admin-manager roles (EmployeeProfile with role=Admin or Manager)
        try:
            from apps.Employee.models import EmployeeProfile

            role_values = [EmployeeProfile.RoleChoices.ADMIN]
            manager_role = getattr(EmployeeProfile.RoleChoices, "MANAGER", None)
            if manager_role:
                role_values.append(manager_role)

            admin_managers = User.objects.filter(
                employeeprofile__role__in=role_values
            ).distinct()
            notify_users.extend(admin_managers)
        except Exception as exc:
            logger.exception(f"Failed to fetch admin-managers for decline notification: {exc}")
        
        # Deduplicate notification recipients
        notify_users = list(set([u for u in notify_users if u]))
        
        # Send notifications
        notify_title = "Service Declined"
        notify_body = (
            f"{user.get_full_name() or user.username} declined "
            f"{cs.service.name} for {cs.client.first_name} {cs.client.last_name}. "
            f"Reason: {reason or 'Not provided'}"
        )
        
        for recipient in notify_users:
            try:
                Notification.objects.create(
                    user=recipient,
                    title=notify_title,
                    message=notify_body,
                )
                send_push_to_user(recipient, notify_title, notify_body)
            except Exception as exc:
                logger.exception(f"Failed to notify user {recipient.id} of decline: {exc}")
        
        # Transfer reminders: find pending ScheduledTask entries tied to this ClientService + previous_employee
        # and reassign to assigner or first admin-manager
        reminder_transfer_target = None
        if last_assignment and last_assignment.assigned_by:
            reminder_transfer_target = last_assignment.assigned_by
        elif notify_users:
            reminder_transfer_target = notify_users[0]
        
        if reminder_transfer_target:
            try:
                from apps.EasyDocs.models import ScheduledTask
                pending_reminders = ScheduledTask.objects.filter(
                    task_type='reminder',
                    status='pending',
                    client_service=cs,
                    assigned_employee=previous_employee,
                )
                transferred_count = pending_reminders.update(
                    assigned_employee=reminder_transfer_target,
                    notes=f"Transferred from {previous_employee} after decline"
                )
                if transferred_count > 0:
                    logger.info(
                        f"Transferred {transferred_count} reminders from employee {previous_employee.id} "
                        f"to {reminder_transfer_target.id} after decline of ClientService {cs.id}"
                    )
            except Exception as exc:
                logger.exception(f"Failed to transfer reminders on decline for ClientService {cs.id}: {exc}")
        
        logger.info(f"ClientService {cs.id} declined by user {user.id}")
        return {
            "success": True,
            "message": "Service declined. The assignment has been unassigned.",
            "client_service_id": cs.id,
            "assignment_status": cs.assignment_status,
        }


def get_pending_assignments_for_user(user) -> List[ClientService]:
    """
    Fetch all ClientService records assigned to user with status pending_acceptance or reassigned.
    
    Args:
        user: User instance
        
    Returns:
        QuerySet of ClientService objects
    """
    return ClientService.objects.filter(
        assigned_employee=user,
        assignment_status__in=('pending_acceptance', 'reassigned')
    ).select_related('service', 'client').order_by('-requested_at')


def notify_reassignment_candidates(
    client_service,
    candidates: List,
    reason: Optional[str] = None
):
    """
    Notify a list of users that a service is available for reassignment.
    
    Args:
        client_service: ClientService instance
        candidates: list of User instances to notify
        reason: optional context for the reassignment opportunity
    """
    if not candidates:
        return
    
    notify_title = "Service Available for Reassignment"
    notify_body = (
        f"{client_service.service.name} for {client_service.client.first_name} "
        f"{client_service.client.last_name} is now available. "
        f"{reason or 'Previous assignee declined.'}"
    )
    
    for candidate in candidates:
        try:
            Notification.objects.create(
                user=candidate,
                title=notify_title,
                message=notify_body,
            )
            send_push_to_user(candidate, notify_title, notify_body)
        except Exception as exc:
            logger.exception(f"Failed to notify reassignment candidate {candidate.id}: {exc}")
