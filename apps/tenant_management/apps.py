from django.apps import AppConfig


class TenantManagementConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.tenant_management'
    def ready(self):
        import apps.tenant_management.signals
