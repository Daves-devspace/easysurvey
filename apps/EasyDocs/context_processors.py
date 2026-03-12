from .models import SiteSettings
from apps.Employee.models import EmployeeProfile
from django.contrib.auth.decorators import user_passes_test
import logging
from django.db import ProgrammingError, OperationalError
from django.templatetags.static import static
from django.utils.functional import SimpleLazyObject
from django.core.cache import cache

def get_fallback_settings():
    return SiteSettings(company_name="Plotsync")


def site_settings(request):
    try:
        settings = SiteSettings.objects.first()
    except (ProgrammingError, OperationalError):
        settings = None
    except Exception:
        logger.exception("Failed to load SiteSettings; using fallback settings.")
        settings = None

    if not settings:
        settings = get_fallback_settings()

    logo_ts = None
    logo_url = None

    logo_field = getattr(settings, 'logo', None)

    if logo_field:
        try:
            mtime = logo_field.storage.get_modified_time(logo_field.name)
            logo_ts = int(mtime.timestamp())
            logo_url = logo_field.url
        except Exception:
            logger.exception("Failed to resolve SiteSettings logo; using fallback logo.")

    # Fallback logo
    if not logo_url:
        logo_url = static('assets/images/plotsync.png')  # Fallback image path

    # Fallback company name
    company_name = settings.company_name if settings and settings.company_name else "Plotsync"

    return {
        'site_settings': settings,
        'logo_ts': logo_ts,
        'logo_url': logo_url,
        'company_name': company_name,
    }
    



logger = logging.getLogger(__name__)

def employee_profile_context(request):
    """
    Adds the logged-in user's EmployeeProfile (if any) to the template context
    for dynamic display in headers, dropdowns, etc.
    """
    profile = None
    avatar_url = None
    role = 'Guest'

    user = getattr(request, 'user', None)

    try:
        is_authenticated = bool(getattr(user, 'is_authenticated', False))
    except Exception:
        is_authenticated = False

    if is_authenticated:
        try:
            profile = user.employeeprofile

            # Use the model's helper method for validation
            avatar_url = profile.get_avatar_url()

            # Get role display
            role = profile.get_role_display() if profile.role else 'Employee'

        except EmployeeProfile.DoesNotExist:
            # For superusers or users without employee profiles
            try:
                role = 'Administrator' if bool(getattr(user, 'is_superuser', False)) else 'User'
            except Exception:
                role = 'User'
    
    # Fallback avatar if no valid profile picture
    if not avatar_url:
        avatar_url = static('assets/images/user/avatar-2.jpg')
    
    return {
        'employee_profile': profile,
        'employee_avatar_url': avatar_url,
        'employee_role': role,
    }
    
    


def sms_balance(request):
    """
    Returns the last known SMS balance from the cache.
    This does NOT hit the API, so it is fast enough to run on every page load.
    """
    # 'global_sms_balance' is updated by MobileSasaAPI.get_balance()
    # in apps/EasyDocs/utils.py
    try:
        balance = cache.get('global_sms_balance')
    except Exception:
        logger.exception("Failed to read SMS balance from cache.")
        balance = None
    
    return {
        'sms_balance': balance
    }