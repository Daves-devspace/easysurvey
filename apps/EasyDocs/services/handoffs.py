"""
apps/EasyDocs/services/handoffs.py

Document handoff assignment and acceptance logic.

Responsibilities:
- create_document_handoff: assign a document to an employee
- handle_accept_handoff: employee accepts a document handoff
- handle_decline_handoff: employee declines a document handoff
- get_pending_handoffs_for_user: fetch all pending handoffs for user
- check_expired_handoffs: find handoffs past max_acceptance_time
- escalate_handoff: escalate unaccepted handoff to admin/manager

Design notes:
- Handoffs use GenericForeignKey to support ClientDoc and Document (office docs)
- 1-day auto-escalation: if not accepted within max_acceptance_time, escalate to admin/manager
- Audit trail via DocumentHandoffLog for all actions
- Notifications sent to assigned employee, assigner, and admins as appropriate
"""

import logging
from typing import List, Dict, Optional
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from apps.EasyDocs.models import (
    DocumentHandoff,
    DocumentHandoffLog,
    ClientDoc,
    Document,
)
from apps.notifications.models import Notification
from apps.notifications.utils import send_push_to_user

logger = logging.getLogger(__name__)
User = get_user_model()


def _admin_manager_role_values() -> List[str]:
    from apps.Employee.models import EmployeeProfile

    roles = [EmployeeProfile.RoleChoices.ADMIN]
    manager_role = getattr(EmployeeProfile.RoleChoices, 'MANAGER', None)
    if manager_role:
        roles.append(manager_role)
    return roles


def _document_display_name(document) -> str:
    doc_type = getattr(document, 'doc_type', None)
    if doc_type is not None and getattr(doc_type, 'name', None):
        return doc_type.name
    return (
        getattr(document, 'doc_name', None)
        or getattr(document, 'title', None)
        or getattr(document, 'name', None)
        or f"Document #{getattr(document, 'id', 'N/A')}"
    )


def serialize_handoff(handoff: DocumentHandoff) -> Dict[str, any]:
    return {
        "id": handoff.id,
        "status": handoff.status,
        "status_display": handoff.get_status_display(),
        "assigned_to_id": handoff.assigned_to_id,
        "assigned_to_name": handoff.assigned_to.get_full_name() or handoff.assigned_to.username,
        "assigned_by_id": handoff.assigned_by_id,
        "assigned_by_name": handoff.assigned_by.get_full_name() or handoff.assigned_by.username,
        "assigned_at": handoff.assigned_at.isoformat() if handoff.assigned_at else None,
        "accepted_at": handoff.accepted_at.isoformat() if handoff.accepted_at else None,
        "max_acceptance_time": handoff.max_acceptance_time.isoformat() if handoff.max_acceptance_time else None,
        "content_type": handoff.content_type.model,
        "object_id": handoff.object_id,
        "client_id": handoff.client_id,
        "notes": handoff.notes,
    }


def create_document_handoff(
    document,
    assigned_to,
    assigned_by,
    client=None,
    notes: str = ""
) -> Dict[str, any]:
    """
    Create a new document handoff assignment.
    
    Args:
        document: Document or ClientDoc instance
        assigned_to: User instance to receive the document
        assigned_by: User instance assigning the document
        client: Optional Client instance (for ClientDoc)
        notes: Optional notes about the handoff
        
    Returns:
        dict with keys: success (bool), message (str), handoff (optional)
    """
    try:
        content_type = ContentType.objects.get_for_model(document)
        if client is None and isinstance(document, ClientDoc):
            client = getattr(document, 'client', None)
        
        with transaction.atomic():
            # Reassign any active handoff on this same document
            active_qs = (
                DocumentHandoff.objects
                .select_for_update()
                .filter(
                    content_type=content_type,
                    object_id=document.id,
                    status__in=['pending', 'accepted']
                )
                .select_related('assigned_to')
                .order_by('-assigned_at')
            )

            already_pending = active_qs.filter(
                assigned_to=assigned_to,
                status='pending'
            ).first()
            if already_pending:
                return {
                    "success": True,
                    "message": "Document is already pending acceptance for this user.",
                    "handoff": serialize_handoff(already_pending),
                }

            for old_handoff in active_qs:
                previous_assignee = old_handoff.assigned_to
                old_handoff.status = 'reassigned'
                old_handoff.save(update_fields=['status'])
                DocumentHandoffLog.objects.create(
                    handoff=old_handoff,
                    action='reassigned',
                    actor=assigned_by,
                    notes=(
                        f"Reassigned from {previous_assignee.get_full_name() or previous_assignee.username} "
                        f"to {assigned_to.get_full_name() or assigned_to.username}"
                    ),
                )

            # Create handoff
            handoff = DocumentHandoff.objects.create(
                content_type=content_type,
                object_id=document.id,
                client=client,
                assigned_to=assigned_to,
                assigned_by=assigned_by,
                status='pending',
                notes=notes,
            )
            
            # Create log entry
            DocumentHandoffLog.objects.create(
                handoff=handoff,
                action='assigned',
                actor=assigned_by,
                notes=notes or f"Document assigned to {assigned_to.get_full_name() or assigned_to.username}",
            )
            
            # Notify assigned employee
            doc_name = _document_display_name(document)
            client_name = ""
            if client:
                client_name = f" for {client.first_name} {client.last_name}"
            
            notify_title = "New Document Assigned"
            notify_body = (
                f"You have been assigned a document: {doc_name}{client_name}. "
                f"Please review and accept within 24 hours."
            )
            
            try:
                Notification.objects.create(
                    user=assigned_to,
                    title=notify_title,
                    message=notify_body,
                )
                send_push_to_user(assigned_to, notify_title, notify_body)
            except Exception as exc:
                logger.exception(f"Failed to notify assigned employee {assigned_to.id} of handoff: {exc}")
            
            logger.info(
                f"Created DocumentHandoff {handoff.id}: {doc_name} assigned to user {assigned_to.id} "
                f"by user {assigned_by.id}"
            )
            
            return {
                "success": True,
                "message": "Document handoff created successfully.",
                "handoff": serialize_handoff(handoff),
            }
    except Exception as exc:
        logger.exception(f"Failed to create document handoff: {exc}")
        return {
            "success": False,
            "message": f"Failed to create document handoff: {str(exc)}"
        }


def handle_accept_handoff(
    handoff_id: int,
    user
) -> Dict[str, any]:
    """
    Employee accepts a document handoff.
    
    Args:
        handoff_id: DocumentHandoff PK
        user: accepting User instance
        
    Returns:
        dict with keys: success (bool), message (str), handoff (optional)
    """
    try:
        handoff = DocumentHandoff.objects.select_related(
            'assigned_to', 'assigned_by', 'client'
        ).get(pk=handoff_id)
    except DocumentHandoff.DoesNotExist:
        logger.warning(f"Accept handoff failed: DocumentHandoff {handoff_id} not found")
        return {"success": False, "message": "Document handoff not found."}
    
    # Verify assignment
    if handoff.assigned_to != user:
        logger.warning(
            f"Accept handoff failed: user {user.id} is not assigned to DocumentHandoff {handoff.id}"
        )
        return {"success": False, "message": "This document is not assigned to you."}
    
    # Verify status
    if handoff.status != 'pending':
        logger.info(
            f"Accept handoff failed: DocumentHandoff {handoff.id} status is {handoff.status}, not pending"
        )
        return {
            "success": False,
            "message": f"Cannot accept: status is {handoff.get_status_display()}."
        }
    
    with transaction.atomic():
        # Update handoff status
        handoff.status = 'accepted'
        handoff.accepted_at = timezone.now()
        handoff.save(update_fields=['status', 'accepted_at'])
        
        # Create log entry
        DocumentHandoffLog.objects.create(
            handoff=handoff,
            action='accepted',
            actor=user,
            notes=f"Accepted by {user.get_full_name() or user.username}",
        )
        
        # Notify assigner
        if handoff.assigned_by:
            doc_name = "Document"
            try:
                doc = handoff.document
                doc_name = getattr(doc, 'name', None) or getattr(doc, 'doc_type', 'Document')
            except Exception:
                pass
            
            client_name = ""
            if handoff.client:
                client_name = f" for {handoff.client.first_name} {handoff.client.last_name}"
            
            notify_title = "Document Handoff Accepted"
            notify_body = (
                f"{user.get_full_name() or user.username} accepted the document handoff: "
                f"{doc_name}{client_name}."
            )
            
            try:
                Notification.objects.create(
                    user=handoff.assigned_by,
                    title=notify_title,
                    message=notify_body,
                )
                send_push_to_user(handoff.assigned_by, notify_title, notify_body)
            except Exception as exc:
                logger.exception(f"Failed to notify assigner {handoff.assigned_by.id} of handoff acceptance: {exc}")
        
        logger.info(f"DocumentHandoff {handoff.id} accepted by user {user.id}")
        return {
            "success": True,
            "message": "Document handoff accepted successfully.",
            "handoff": serialize_handoff(handoff),
        }


def handle_decline_handoff(
    handoff_id: int,
    user,
    reason: Optional[str] = None
) -> Dict[str, any]:
    """
    Employee declines a document handoff.
    
    Per requirements:
    - Notify assigner and admin/manager roles
    - Log the decline with reason
    
    Args:
        handoff_id: DocumentHandoff PK
        user: declining User instance
        reason: optional decline reason
        
    Returns:
        dict with keys: success (bool), message (str), handoff (optional)
    """
    try:
        handoff = DocumentHandoff.objects.select_related(
            'assigned_to', 'assigned_by', 'client'
        ).get(pk=handoff_id)
    except DocumentHandoff.DoesNotExist:
        logger.warning(f"Decline handoff failed: DocumentHandoff {handoff_id} not found")
        return {"success": False, "message": "Document handoff not found."}
    
    # Verify assignment
    if handoff.assigned_to != user:
        logger.warning(
            f"Decline handoff failed: user {user.id} is not assigned to DocumentHandoff {handoff.id}"
        )
        return {"success": False, "message": "This document is not assigned to you."}
    
    # Verify status
    if handoff.status != 'pending':
        logger.info(
            f"Decline handoff failed: DocumentHandoff {handoff.id} status is {handoff.status}, not pending"
        )
        return {
            "success": False,
            "message": f"Cannot decline: status is {handoff.get_status_display()}."
        }
    
    with transaction.atomic():
        # Update handoff status
        handoff.status = 'declined'
        handoff.save(update_fields=['status'])
        
        # Create log entry
        DocumentHandoffLog.objects.create(
            handoff=handoff,
            action='declined',
            actor=user,
            notes=reason or f"Declined by {user.get_full_name() or user.username}",
        )
        
        # Gather notification recipients: assigner + admin/managers
        notify_users = []
        if handoff.assigned_by:
            notify_users.append(handoff.assigned_by)
        
        # Also notify admin-manager roles
        try:
            admin_managers = User.objects.filter(
                employeeprofile__role__in=_admin_manager_role_values()
            ).distinct()
            notify_users.extend(admin_managers)
        except Exception as exc:
            logger.exception(f"Failed to fetch admin-managers for decline notification: {exc}")
        
        # Deduplicate
        notify_users = list(set([u for u in notify_users if u]))
        
        # Build notification content
        doc_name = "Document"
        try:
            doc = handoff.document
            doc_name = getattr(doc, 'name', None) or getattr(doc, 'doc_type', 'Document')
        except Exception:
            pass
        
        client_name = ""
        if handoff.client:
            client_name = f" for {handoff.client.first_name} {handoff.client.last_name}"
        
        notify_title = "Document Handoff Declined"
        notify_body = (
            f"{user.get_full_name() or user.username} declined the document handoff: "
            f"{doc_name}{client_name}. "
            f"Reason: {reason or 'Not provided'}"
        )
        
        # Send notifications
        for recipient in notify_users:
            try:
                Notification.objects.create(
                    user=recipient,
                    title=notify_title,
                    message=notify_body,
                )
                send_push_to_user(recipient, notify_title, notify_body)
            except Exception as exc:
                logger.exception(f"Failed to notify user {recipient.id} of handoff decline: {exc}")
        
        logger.info(f"DocumentHandoff {handoff.id} declined by user {user.id}")
        return {
            "success": True,
            "message": "Document handoff declined. The assigner has been notified.",
            "handoff": serialize_handoff(handoff),
        }


def get_pending_handoffs_for_user(user) -> List[DocumentHandoff]:
    """
    Fetch all DocumentHandoff records assigned to user with status=pending.
    
    Args:
        user: User instance
        
    Returns:
        QuerySet of DocumentHandoff objects
    """
    return DocumentHandoff.objects.filter(
        assigned_to=user,
        status='pending'
    ).select_related('assigned_by', 'assigned_to', 'client', 'content_type').order_by('-assigned_at')


def get_latest_handoffs_for_documents(documents) -> Dict[int, DocumentHandoff]:
    doc_list = list(documents)
    if not doc_list:
        return {}

    model = doc_list[0].__class__
    content_type = ContentType.objects.get_for_model(model)
    object_ids = [doc.id for doc in doc_list]

    handoffs = (
        DocumentHandoff.objects
        .filter(content_type=content_type, object_id__in=object_ids)
        .select_related('assigned_to', 'assigned_by', 'client', 'content_type')
        .order_by('object_id', '-assigned_at')
    )

    latest = {}
    for handoff in handoffs:
        if handoff.object_id not in latest:
            latest[handoff.object_id] = handoff
    return latest


def check_expired_handoffs():
    """
    Find all handoffs that have exceeded max_acceptance_time and are still pending.
    
    Returns:
        QuerySet of expired DocumentHandoff objects
    """
    now = timezone.now()
    expired = DocumentHandoff.objects.filter(
        status='pending',
        max_acceptance_time__lt=now
    ).select_related('assigned_to', 'assigned_by', 'client')
    
    return expired


def escalate_handoff(handoff) -> Dict[str, any]:
    """
    Escalate an unaccepted handoff to admin/manager roles.
    
    Called when max_acceptance_time is exceeded.
    
    Args:
        handoff: DocumentHandoff instance
        
    Returns:
        dict with keys: success (bool), message (str), notified_users (list)
    """
    if isinstance(handoff, int):
        try:
            handoff = DocumentHandoff.objects.select_related('assigned_to', 'assigned_by', 'client', 'content_type').get(pk=handoff)
        except DocumentHandoff.DoesNotExist:
            return {
                "success": False,
                "message": "Document handoff not found for escalation."
            }

    if handoff.status != 'pending':
        return {
            "success": False,
            "message": f"Handoff status is {handoff.status}, not pending."
        }
    
    # Fetch admin-managers to escalate to
    try:
        admin_managers = User.objects.filter(
            employeeprofile__role__in=_admin_manager_role_values()
        ).distinct()
    except Exception as exc:
        logger.exception(f"Failed to fetch admin-managers for handoff escalation: {exc}")
        return {
            "success": False,
            "message": "Failed to fetch escalation recipients."
        }
    
    if not admin_managers.exists():
        logger.warning(f"No admin-managers found to escalate DocumentHandoff {handoff.id}")
        return {
            "success": False,
            "message": "No admin-managers available for escalation."
        }
    
    # Build notification content
    doc_name = "Document"
    try:
        doc_name = _document_display_name(handoff.document)
    except Exception:
        pass
    
    client_name = ""
    if handoff.client:
        client_name = f" for {handoff.client.first_name} {handoff.client.last_name}"
    
    assigned_to_name = handoff.assigned_to.get_full_name() or handoff.assigned_to.username
    
    notify_title = "Document Handoff Escalation"
    notify_body = (
        f"ESCALATION: Document handoff {doc_name}{client_name} assigned to "
        f"{assigned_to_name} has not been accepted within 24 hours. "
        f"Assigned at: {handoff.assigned_at.strftime('%Y-%m-%d %H:%M')}. "
        f"Please follow up or reassign."
    )
    
    notified_users = []
    for admin in admin_managers:
        try:
            Notification.objects.create(
                user=admin,
                title=notify_title,
                message=notify_body,
            )
            send_push_to_user(admin, notify_title, notify_body)
            notified_users.append(admin)
        except Exception as exc:
            logger.exception(f"Failed to send escalation notification to admin {admin.id}: {exc}")
    
    # Log the escalation
    try:
        handoff.status = 'reassigned'
        handoff.save(update_fields=['status'])
        DocumentHandoffLog.objects.create(
            handoff=handoff,
            action='reassigned',  # using reassigned to log escalation event
            actor=None,  # system-triggered
            notes=f"Escalated to admin/manager roles after exceeding max_acceptance_time. Notified: {', '.join([u.username for u in notified_users])}",
        )
    except Exception as exc:
        logger.exception(f"Failed to create escalation log for DocumentHandoff {handoff.id}: {exc}")
    
    logger.info(
        f"Escalated DocumentHandoff {handoff.id} to {len(notified_users)} admin-managers "
        f"after exceeding max_acceptance_time"
    )
    
    return {
        "success": True,
        "message": f"Handoff escalated to {len(notified_users)} admin-managers.",
        "notified_users": [u.id for u in notified_users],
        "handoff": serialize_handoff(handoff),
    }
