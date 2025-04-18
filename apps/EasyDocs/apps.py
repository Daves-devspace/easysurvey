from django.apps import AppConfig



class EasyDocsConfig(AppConfig):
    name = 'apps.EasyDocs'

    def ready(self):
        import apps.EasyDocs.signals  # Registers signal handlers
