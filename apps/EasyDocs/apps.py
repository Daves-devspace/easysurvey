from django.apps import AppConfig
from django.db.models.signals import post_migrate

class EasyDocsConfig(AppConfig):
    name = 'apps.EasyDocs'
    label = 'easydocs'

    def ready(self):
        from .utils import load_email_settings

        def safe_load_email_settings(sender, **kwargs):
            try:
                load_email_settings()
            except Exception as e:
                import logging
                logging.warning(f"Could not load email settings: {e}")

        post_migrate.connect(safe_load_email_settings, sender=self)



# from django.apps import AppConfig
#
# # apps.py
# class CoreConfig(AppConfig):
#     name = 'apps.EasyDocs'
#     def ready(self):
#         from .utils import load_email_settings
#         load_email_settings()
#
#
# class EasyDocsConfig(AppConfig):
#     name = 'apps.EasyDocs'
#
#     def ready(self):
#         import apps.EasyDocs.signals  # Registers signal handlers
