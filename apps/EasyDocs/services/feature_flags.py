from apps.EasyDocs.models import SiteSettings


def get_site_settings():
    settings = SiteSettings.objects.first()
    if settings is None:
        settings = SiteSettings()
    return settings


def is_task_assigning_enabled() -> bool:
    settings = get_site_settings()
    return bool(getattr(settings, 'allow_task_assigning', False))


def is_document_assigning_enabled() -> bool:
    settings = get_site_settings()
    return bool(getattr(settings, 'allow_document_assigning', False))


def is_service_tracking_enabled() -> bool:
    settings = get_site_settings()
    return bool(getattr(settings, 'allow_service_tracking', True))
