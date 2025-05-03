from .models import SiteSettings
from django.db import ProgrammingError, OperationalError

def site_settings(request):
    try:
        settings = SiteSettings.objects.first()
    except (ProgrammingError, OperationalError):
        # Table does not exist or DB not ready
        settings = None

    logo_ts = None
    if settings and settings.logo:
        try:
            mtime = settings.logo.storage.get_modified_time(settings.logo.name)
            logo_ts = int(mtime.timestamp())
        except Exception:
            logo_ts = None

    return {
        'site_settings': settings,
        'logo_ts': logo_ts,
    }
