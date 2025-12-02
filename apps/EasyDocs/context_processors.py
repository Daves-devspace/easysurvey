from .models import SiteSettings
from apps.Employee.models import EmployeeProfile
from django.contrib.auth.decorators import user_passes_test
import logging
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
    



logger = logging.getLogger(__name__)

def employee_profile_context(request):
    """
    Adds the logged-in user's EmployeeProfile (if any) to the template context
    for dynamic display in headers, dropdowns, etc.
    """
    profile = None
    avatar_url = None
    role = 'Guest'
    
    if request.user.is_authenticated:
        try:
            profile = request.user.employeeprofile
            
            # Use the model's helper method for validation
            avatar_url = profile.get_avatar_url()
            
            # Get role display
            role = profile.get_role_display() if profile.role else 'Employee'
            
        except EmployeeProfile.DoesNotExist:
            # For superusers or users without employee profiles
            if request.user.is_superuser:
                role = 'Administrator'
            else:
                role = 'User'
    
    # Fallback avatar if no valid profile picture
    if not avatar_url:
        avatar_url = static('assets/images/user/avatar-2.jpg')
    
    return {
        'employee_profile': profile,
        'employee_avatar_url': avatar_url,
        'employee_role': role,
    }