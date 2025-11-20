
from apps.Employee.config.roles_config import  ROLE_PERMISSIONS

from django.core.exceptions import PermissionDenied

from apps.Employee.models import EmployeeProfile


def enforce_permission(user, model_class, action):
    """
    Enforce permission for a user on a model and action.
    Raises PermissionDenied if the permission is not granted.
    """
    if model_class is None:
        raise ValueError("model_class must not be None.")

    if not action:
        raise ValueError("Action must be specified.")

    if user.is_superuser:
        return  # Always allowed

    if not user.is_authenticated:
        raise PermissionDenied("You must be logged in to perform this action.")

    try:
        role = user.employeeprofile.role
    except EmployeeProfile.DoesNotExist:
        raise PermissionDenied("No role profile assigned to user.")

    role_permissions = ROLE_PERMISSIONS.get(role, {}).get('permissions', {})
    allowed_actions = role_permissions.get(model_class, [])

    if action in allowed_actions:
        return  # ✅ Permission granted

    raise PermissionDenied(f"You do not have permission to {action} {model_class.__name__}.")


