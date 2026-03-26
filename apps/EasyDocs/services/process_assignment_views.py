"""
HTTP endpoint handlers for per-process-assignment actions.

All views require:
  - Authenticated user (@login_required)
  - POST method only
  - CSRF enforced (default Django middleware)

Assignee actions (accept / decline / complete):
  - assignment.assignee must equal request.user

Admin/manager action (assign users to a step):
  - request.user must be staff, superuser, or have the
    'easydocs.change_clientserviceprocessassignment' permission
"""
import json
import logging

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from apps.EasyDocs.services.process_assignments import (
    handle_accept_process_assignment,
    handle_decline_process_assignment,
    handle_complete_process_assignment,
    handle_assign_users_to_process_step,
)
from apps.EasyDocs.services.feature_flags import is_task_assigning_enabled

logger = logging.getLogger(__name__)


def _is_admin_or_manager(user):
    if user.is_staff or user.is_superuser:
        return True
    if user.has_perm("easydocs.change_clientserviceprocessassignment"):
        return True
    try:
        role = user.employeeprofile.role
        return role in ("admin", "manager")
    except Exception:
        return False


def _json_body(request) -> dict:
    """Parse JSON body; return empty dict on failure."""
    try:
        return json.loads(request.body)
    except (json.JSONDecodeError, Exception):
        return {}


def _request_data(request) -> dict:
    """Parse request payload from JSON or form POST."""
    content_type = (request.content_type or "").lower()
    if "application/json" in content_type:
        return _json_body(request)

    # form-encoded fallback
    data = dict(request.POST)
    flattened = {}
    for key, value in data.items():
        if isinstance(value, list) and len(value) == 1:
            flattened[key] = value[0]
        else:
            flattened[key] = value

    # normalize user_ids for form submissions
    if "user_ids" not in flattened:
        user_ids_list = request.POST.getlist("user_ids")
        if user_ids_list:
            flattened["user_ids"] = user_ids_list

    return flattened


def _wants_json(request) -> bool:
    content_type = (request.content_type or "").lower()
    accept = (request.headers.get("Accept") or "").lower()
    requested_with = (request.headers.get("X-Requested-With") or "").lower()
    return (
        "application/json" in content_type
        or "application/json" in accept
        or requested_with == "xmlhttprequest"
    )


def _respond(request, result: dict, *, extra: dict = None, fail_status: int = 403):
    """Return JSON for API requests, or redirect back with Django messages for HTML forms."""
    payload = {
        "success": result.get("success", False),
        "message": result.get("message", ""),
    }
    if extra:
        payload.update(extra)

    if _wants_json(request):
        status_code = 200 if payload["success"] else fail_status
        return JsonResponse(payload, status=status_code)

    if payload["success"]:
        messages.success(request, payload["message"])
    else:
        messages.error(request, payload["message"])
    return redirect(request.META.get("HTTP_REFERER", "/"))


# ---------------------------------------------------------------------------
# Assignee endpoints
# ---------------------------------------------------------------------------

@login_required
@require_POST
def accept_process_assignment(request, assignment_id: int):
    if not is_task_assigning_enabled():
        return _respond(
            request,
            {"success": False, "message": "Task assigning is currently disabled."},
            fail_status=403,
        )

    body = _request_data(request)
    reason = body.get("reason", "")

    result = handle_accept_process_assignment(
        assignment_id=assignment_id,
        user=request.user,
        reason=reason,
    )
    return _respond(
        request,
        result,
        extra={"assignment_id": result.get("assignment_id")},
        fail_status=403,
    )


@login_required
@require_POST
def decline_process_assignment(request, assignment_id: int):
    if not is_task_assigning_enabled():
        return _respond(
            request,
            {"success": False, "message": "Task assigning is currently disabled."},
            fail_status=403,
        )

    body = _request_data(request)
    reason = body.get("reason", "")

    result = handle_decline_process_assignment(
        assignment_id=assignment_id,
        user=request.user,
        reason=reason,
    )

    return _respond(
        request,
        result,
        extra={"assignment_id": result.get("assignment_id")},
        fail_status=403,
    )


@login_required
@require_POST
def complete_process_assignment(request, assignment_id: int):
    if not is_task_assigning_enabled():
        return _respond(
            request,
            {"success": False, "message": "Task assigning is currently disabled."},
            fail_status=403,
        )

    body = _request_data(request)
    note = body.get("note", "")

    result = handle_complete_process_assignment(
        assignment_id=assignment_id,
        user=request.user,
        note=note,
    )

    return _respond(
        request,
        result,
        extra={
            "step_completed": result.get("step_completed", False),
            "accepted_count": result.get("accepted_count"),
            "completed_count": result.get("completed_count"),
            "assignment_id": result.get("assignment", {}) and getattr(result.get("assignment"), "id", None),
        },
        fail_status=403,
    )


# ---------------------------------------------------------------------------
# Admin / manager endpoint
# ---------------------------------------------------------------------------

@login_required
@require_POST
def assign_users_to_process_step(request, process_step_id: int):
    if not is_task_assigning_enabled():
        return _respond(
            request,
            {"success": False, "message": "Task assigning is currently disabled."},
            fail_status=403,
        )

    if not _is_admin_or_manager(request.user):
        return _respond(request, {"success": False, "message": "Permission denied."}, fail_status=403)

    body = _request_data(request)
    user_ids = body.get("user_ids", [])
    reason = body.get("reason", "")

    if isinstance(user_ids, str) and "application/json" not in (request.content_type or "").lower():
        user_ids = [u.strip() for u in user_ids.split(',') if u.strip()]

    if not isinstance(user_ids, list):
        return _respond(request, {"success": False, "message": "'user_ids' must be a list."}, fail_status=400)

    result = handle_assign_users_to_process_step(
        process_step_id=process_step_id,
        user_ids=user_ids,
        assigned_by=request.user,
        reason=reason,
    )

    return _respond(
        request,
        result,
        extra={
            "created": result.get("created", 0),
            "updated": result.get("updated", 0),
            "deactivated": result.get("deactivated", 0),
            "step_id": result.get("step_id") or (result.get("step") and result["step"].id),
        } if result.get("success") else None,
        fail_status=400,
    )
