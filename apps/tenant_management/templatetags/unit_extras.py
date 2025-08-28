# apps/tenant_management/templatetags/unit_extras.py
from django import template
from django.db.models import ObjectDoesNotExist

register = template.Library()

@register.filter
def has_lease(unit):
    """Check if unit has a lease without raising an exception"""
    try:
        return unit.lease is not None
    except ObjectDoesNotExist:
        return False

@register.filter
def get_lease(unit):
    """Safely get unit lease or return None"""
    try:
        return unit.lease
    except ObjectDoesNotExist:
        return None

@register.filter
def has_tenant(unit):
    """Check if unit has a tenant without raising an exception"""
    try:
        return unit.lease and unit.lease.tenant is not None
    except ObjectDoesNotExist:
        return False

@register.filter
def get_tenant(unit):
    """Safely get unit tenant or return None"""
    try:
        if unit.lease and unit.lease.tenant:
            return unit.lease.tenant
        return None
    except ObjectDoesNotExist:
        return None

@register.filter
def get_tenant_name(unit):
    """Safely get unit tenant full name or return empty string"""
    try:
        if unit.lease and unit.lease.tenant:
            return unit.lease.tenant.full_name
        return ""
    except ObjectDoesNotExist:
        return ""

@register.filter
def get_lease_start_date(unit):
    """Safely get lease start date or return None"""
    try:
        if unit.lease:
            return unit.lease.start_date
        return None
    except ObjectDoesNotExist:
        return None