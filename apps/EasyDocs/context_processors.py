# your_app/context_processors.py
from .models import SiteSettings

def site_settings(request):
    settings = SiteSettings.objects.first()
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
