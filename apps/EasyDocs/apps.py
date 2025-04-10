from django.apps import AppConfig


# class EasydocsConfig(AppConfig):
#     default_auto_field = 'django.db.models.BigAutoField'
#     name = 'apps.EasyDocs'
#


class EasyDocsConfig(AppConfig):
    name = 'apps.EasyDocs'

    def ready(self):
        import apps.EasyDocs.signals  # Registers signal handlers
