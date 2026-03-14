"""
apps/EasyDocs/services/reminders.py

Reminder scheduling and deadline management logic.

Responsibilities:
- schedule_service_reminders: create reminder tasks when service is accepted
- cancel_service_reminders: cancel pending reminders when service completes/declines
- reschedule_service_reminders: update reminder times when deadline extends
- process_deadline_extension: handle deadline extension request with validation

Design notes:
- Reminders are stored as ScheduledTask records with task_type='reminder'
- Reminder schedule: 50% of time until deadline, 75%, 90%, and at deadline
- Celery task IDs follow pattern: reminder_{client_service_id}_{percentage}
- Extensions must be requested by assigned employee, require reason, limited count
"""

import logging
from typing import List, Dict, Optional
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.EasyDocs.models import (
    ClientService,
    ScheduledTask,
    ServiceDeadlineExtension,
)

logger = logging.getLogger(__name__)
User = get_user_model()

# Reminder schedule: percentages of time elapsed until deadline
REMINDER_SCHEDULE = [50, 75, 90, 100]  # 50%, 75%, 90%, at deadline


def schedule_service_reminders(
    client_service: ClientService,
    assigned_employee = None
) -> List[ScheduledTask]:
    """
    Schedule reminder tasks for a client service based on its deadline.
    
    Reminders are scheduled at 50%, 75%, 90%, and 100% of time until deadline.
    
    Args:
        client_service: ClientService instance with deadline set
        assigned_employee: Employee to receive reminders (defaults to cs.assigned_employee)
        
    Returns:
        List of created ScheduledTask instances
    """
    if not client_service.deadline:
        logger.warning(
            f"Cannot schedule reminders for ClientService {client_service.id}: no deadline set"
        )
        return []
    
    employee = assigned_employee or client_service.assigned_employee
    if not employee:
        logger.warning(
            f"Cannot schedule reminders for ClientService {client_service.id}: no assigned employee"
        )
        return []
    
    # Calculate time window from now to deadline
    now = timezone.now()
    deadline = client_service.deadline
    
    if deadline <= now:
        logger.info(
            f"Deadline for ClientService {client_service.id} is in the past, skipping reminders"
        )
        return []
    
    base_time = client_service.requested_at or now
    total_duration = (deadline - base_time).total_seconds()
    
    created_tasks = []
    
    for percentage in REMINDER_SCHEDULE:
        # Calculate scheduled time
        elapsed_seconds = (total_duration * percentage) / 100.0
        scheduled_time = base_time + timedelta(seconds=elapsed_seconds)
        
        # Skip reminders in the past
        if scheduled_time <= now:
            logger.debug(
                f"Skipping {percentage}% reminder for ClientService {client_service.id}: "
                f"scheduled_time {scheduled_time} is in the past"
            )
            continue
        
        # Generate unique task_id
        task_id = f"reminder_{client_service.id}_{percentage}"
        
        # Check if this reminder already exists
        existing = ScheduledTask.objects.filter(task_id=task_id, status='pending').first()
        if existing:
            logger.debug(f"Reminder {task_id} already exists, skipping creation")
            continue
        
        # Create reminder message
        if percentage == 100:
            task_name = f"Deadline Reached: {client_service.service.name}"
            message = (
                f"DEADLINE REACHED: Service '{client_service.service.name}' "
                f"for {client_service.client.first_name} {client_service.client.last_name} "
                f"(Land: {client_service.land_description}) is due now."
            )
        else:
            task_name = f"{percentage}% Reminder: {client_service.service.name}"
            message = (
                f"REMINDER ({percentage}%): Service '{client_service.service.name}' "
                f"for {client_service.client.first_name} {client_service.client.last_name} "
                f"(Land: {client_service.land_description}) deadline approaching. "
                f"Due: {deadline.strftime('%Y-%m-%d %H:%M')}"
            )
        
        # Create ScheduledTask
        try:
            task = ScheduledTask.objects.create(
                task_id=task_id,
                task_name=task_name,
                task_type='reminder',
                scheduled_time=scheduled_time,
                message_preview=message,
                status='pending',
                client_service=client_service,
                assigned_employee=employee,
                notes=f"Auto-scheduled {percentage}% reminder",
                payload={
                    'client_service_id': client_service.id,
                    'employee_id': employee.id,
                    'percentage': percentage,
                    'deadline': deadline.isoformat(),
                    'message': message,
                }
            )
            created_tasks.append(task)
            logger.info(
                f"Scheduled {percentage}% reminder for ClientService {client_service.id} "
                f"at {scheduled_time} (task_id: {task_id})"
            )
        except Exception as exc:
            logger.exception(
                f"Failed to create reminder task {task_id} for ClientService {client_service.id}: {exc}"
            )
    
    return created_tasks


def cancel_service_reminders(
    client_service: ClientService,
    reason: str = "Service completed or declined"
) -> int:
    """
    Cancel all pending reminders for a client service.
    
    Args:
        client_service: ClientService instance
        reason: Cancellation reason
        
    Returns:
        Number of reminders cancelled
    """
    cancelled_count = ScheduledTask.objects.filter(
        task_type='reminder',
        client_service=client_service,
        status='pending',
    ).update(
        status='cancelled',
        notes=reason,
        completed_at=timezone.now(),
    )
    
    if cancelled_count > 0:
        logger.info(
            f"Cancelled {cancelled_count} pending reminders for ClientService {client_service.id}: {reason}"
        )
    
    return cancelled_count


def reschedule_service_reminders(
    client_service: ClientService,
    new_deadline: timezone.datetime,
    assigned_employee = None
) -> List[ScheduledTask]:
    """
    Reschedule reminders after a deadline extension.
    
    Cancels existing pending reminders and creates new ones based on new deadline.
    
    Args:
        client_service: ClientService instance
        new_deadline: New deadline datetime
        assigned_employee: Employee to receive reminders (defaults to cs.assigned_employee)
        
    Returns:
        List of newly created ScheduledTask instances
    """
    # Cancel existing pending reminders
    cancel_service_reminders(
        client_service,
        reason=f"Rescheduling due to deadline extension to {new_deadline}"
    )
    
    # Update client service deadline (caller should do this, but we ensure it here)
    if client_service.deadline != new_deadline:
        client_service.deadline = new_deadline
        client_service.save(update_fields=['deadline'])
    
    # Schedule new reminders
    return schedule_service_reminders(client_service, assigned_employee)


def process_deadline_extension(
    client_service_id: int,
    requesting_user,
    additional_days: int,
    reason: str
) -> Dict[str, any]:
    """
    Process a deadline extension request.
    
    Validation:
    - Only assigned employee can request extension
    - Service must be in accepted status
    - Additional days must be positive
    - Maximum 3 extensions per service (configurable)
    
    Actions:
    - Create ServiceDeadlineExtension record
    - Update ClientService deadline and deadline_extended flag
    - Reschedule reminders
    - Notify assigner/admins
    
    Args:
        client_service_id: ClientService PK
        requesting_user: User requesting extension
        additional_days: Number of days to extend
        reason: Justification for extension
        
    Returns:
        dict with keys: success (bool), message (str), new_deadline (optional datetime)
    """
    # Validation
    try:
        cs = ClientService.objects.select_related(
            'assigned_employee', 'service', 'client'
        ).get(pk=client_service_id)
    except ClientService.DoesNotExist:
        logger.warning(f"Deadline extension failed: ClientService {client_service_id} not found")
        return {"success": False, "message": "Service not found."}
    
    # Check assignment
    if cs.assigned_employee != requesting_user:
        logger.warning(
            f"Deadline extension failed: user {requesting_user.id} is not assigned to ClientService {cs.id}"
        )
        return {"success": False, "message": "You are not assigned to this service."}
    
    # Check status
    if cs.assignment_status != 'accepted':
        logger.info(
            f"Deadline extension failed: ClientService {cs.id} status is {cs.assignment_status}, not accepted"
        )
        return {
            "success": False,
            "message": f"Cannot extend deadline: service status is {cs.get_assignment_status_display()}."
        }
    
    # Check deadline exists
    if not cs.deadline:
        return {"success": False, "message": "No deadline set for this service."}
    
    # Validate additional_days
    if additional_days <= 0:
        return {"success": False, "message": "Additional days must be positive."}
    
    if additional_days > 30:
        return {"success": False, "message": "Extension cannot exceed 30 days at once."}
    
    # Check extension count limit
    existing_extensions = ServiceDeadlineExtension.objects.filter(
        client_service=cs
    ).count()
    
    MAX_EXTENSIONS = 3
    if existing_extensions >= MAX_EXTENSIONS:
        return {
            "success": False,
            "message": f"Maximum {MAX_EXTENSIONS} extensions allowed. Please contact management."
        }
    
    # Calculate new deadline
    old_deadline = cs.deadline
    new_deadline = old_deadline + timedelta(days=additional_days)
    
    # Perform extension
    with transaction.atomic():
        # Update ClientService
        cs.deadline = new_deadline
        cs.deadline_extended = True
        if not cs.original_deadline:
            cs.original_deadline = old_deadline
        cs.save(update_fields=['deadline', 'deadline_extended', 'original_deadline'])
        
        # Create extension record
        extension = ServiceDeadlineExtension.objects.create(
            client_service=cs,
            old_deadline=old_deadline,
            new_deadline=new_deadline,
            extended_by=requesting_user,
            reason=reason,
        )
        
        # Reschedule reminders
        try:
            reschedule_service_reminders(cs, new_deadline, cs.assigned_employee)
        except Exception as exc:
            logger.exception(
                f"Failed to reschedule reminders after deadline extension for ClientService {cs.id}: {exc}"
            )
            # Don't fail the extension if reminder rescheduling fails
        
        # Notify assigner and admins
        try:
            from apps.EasyDocs.models import ServiceAssignmentLog
            from apps.notifications.models import Notification
            from apps.notifications.utils import send_push_to_user
            
            # Find assigner
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
            
            # Also notify admin-managers
            try:
                from apps.Employee.models import EmployeeProfile
                admin_managers = User.objects.filter(
                    employeeprofile__role__in=[
                        EmployeeProfile.RoleChoices.ADMIN,
                        EmployeeProfile.RoleChoices.MANAGER
                    ]
                ).distinct()
                notify_users.extend(admin_managers)
            except Exception:
                pass
            
            # Deduplicate
            notify_users = list(set([u for u in notify_users if u]))
            
            # Send notifications
            notify_title = "Deadline Extended"
            notify_body = (
                f"{requesting_user.get_full_name() or requesting_user.username} extended deadline for "
                f"{cs.service.name} (Client: {cs.client.first_name} {cs.client.last_name}) "
                f"by {additional_days} days. New deadline: {new_deadline.strftime('%Y-%m-%d %H:%M')}. "
                f"Reason: {reason}"
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
                    logger.exception(f"Failed to notify user {recipient.id} of deadline extension: {exc}")
        except Exception as exc:
            logger.exception(f"Failed to send deadline extension notifications: {exc}")
        
        logger.info(
            f"Deadline extended for ClientService {cs.id} by {additional_days} days. "
            f"Old: {old_deadline}, New: {new_deadline}"
        )
        
        return {
            "success": True,
            "message": f"Deadline extended by {additional_days} days successfully.",
            "new_deadline": new_deadline,
            "extension_id": extension.id,
        }
