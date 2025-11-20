# apps/employee/templatetags/user_extras.py

from django import template

register = template.Library()

@register.simple_tag
def display_user_role(user):
    if user.is_superuser:
        return "Superuser"

    try:
        profile = user.employeeprofile
        if profile.role:
            return profile.get_role_display()
        else:
            return "No Role Assigned"
    except AttributeError:
        return "No Profile"
