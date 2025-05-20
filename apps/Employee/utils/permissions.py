from django.core.exceptions import PermissionDenied
from django.contrib.auth.models import Group
from apps.Employee.config.roles_config import  ROLE_PERMISSIONS

def enforce_permission(user, model_class, action):
    """
    Enforce permission for a user on a model and action.
    Raises PermissionDenied if the permission is not granted.
    """
    if user.is_superuser:
        return  # Always allowed

    if not user.is_authenticated:
        raise PermissionDenied("You must be logged in.")

    user_groups = user.groups.values_list('name', flat=True)
    model_name = model_class.__name__.lower()

    for group in user_groups:
        perms = ROLE_PERMISSIONS.get(group, {}).get(model_name, [])
        if action in perms:
            return  # Permission granted

    raise PermissionDenied(f"You do not have permission to {action} {model_name}.")
