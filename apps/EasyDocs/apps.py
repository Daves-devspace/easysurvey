from django.apps import AppConfig
from django.db.models.signals import post_migrate
from django.conf import settings


class EasyDocsConfig(AppConfig):
    name = 'apps.EasyDocs'
    label = 'easydocs'

    def ready(self):
        import apps.EasyDocs.signals  # Registers signal handlers
