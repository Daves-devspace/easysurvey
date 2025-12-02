from .permissions import enforce_permission

class RolePermissionRequiredMixin:
    """
    A mixin to enforce role-based permissions from config.py in CBVs.
    Must define: model_class and action
    """
    model_class = None
    action = None

    def dispatch(self, request, *args, **kwargs):
        # Auto-set model_class from self.model if not explicitly defined
        if self.model_class is None:
            if hasattr(self, 'model'):
                self.model_class = self.model
            else:
                raise ValueError("model_class must be defined on the view or the mixin.")

        # Validate action
        if not self.action:
            raise ValueError("You must define 'action' in the view using this mixin.")

        # Enforce permission
        enforce_permission(request.user, self.model_class, self.action)
        return super().dispatch(request, *args, **kwargs)
