from django.apps import AppConfig

# apps.py
class CoreConfig(AppConfig):
    name = 'apps.EasyDocs'
    def ready(self):
        from .utils import load_email_settings
        load_email_settings()


class EasyDocsConfig(AppConfig):
    name = 'apps.EasyDocs'

    def ready(self):
        import apps.EasyDocs.signals  # Registers signal handlers
