from .permissions import enforce_permission

class RolePermissionRequiredMixin:
    """
    A mixin to enforce role-based permissions from config.py in CBVs.
    Must define: model_class and action
    """
    model_class = None
    action = None

    def dispatch(self, request, *args, **kwargs):
        enforce_permission(request.user, self.model_class, self.action)
        return super().dispatch(request, *args, **kwargs)
