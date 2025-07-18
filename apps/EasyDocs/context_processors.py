from .models import SiteSettings
from django.db import ProgrammingError, OperationalError
from django.templatetags.static import static
from django.utils.functional import SimpleLazyObject

def get_fallback_settings():
    return SiteSettings(company_name="SMARTSURVEYOR")


def site_settings(request):
    try:
        settings = SiteSettings.objects.first()
    except (ProgrammingError, OperationalError):
        settings = None

    if not settings:
        settings = get_fallback_settings()

    logo_ts = None
    logo_url = None

    if settings.logo:
        try:
            mtime = settings.logo.storage.get_modified_time(settings.logo.name)
            logo_ts = int(mtime.timestamp())
            logo_url = settings.logo.url
        except Exception:
            pass

    # Fallback logo
    if not logo_url:
        logo_url = static('assets/images/pages/smrtlg.png')  # Fallback image path

    # Fallback company name
    company_name = settings.company_name if settings and settings.company_name else "SMARTSURVEYOR"

    return {
        'site_settings': settings,
        'logo_ts': logo_ts,
        'logo_url': logo_url,
        'company_name': company_name,
    }
    
# def site_settings(request):
#     try:
#         settings = SiteSettings.objects.first()
#     except (ProgrammingError, OperationalError):
#         settings = None

#     if not settings:
#         settings = get_fallback_settings()

#     logo_ts = None
#     if settings.logo:
#         try:
#             mtime = settings.logo.storage.get_modified_time(settings.logo.name)
#             logo_ts = int(mtime.timestamp())
#         except Exception:
#             pass

#     return {
#         'site_settings': settings,
#         'logo_ts': logo_ts,
#     }
