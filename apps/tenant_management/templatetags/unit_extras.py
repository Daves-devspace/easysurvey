from django import template
from django.db.models import ObjectDoesNotExist

register = template.Library()


def _get_active_lease(unit):
    """Helper to return the active lease for a unit, or None."""
    try:
        # assuming a boolean field is_active on Lease
        return unit.leases.filter(is_active=True).first()
    except Exception:
        return None


@register.filter
def has_lease(unit):
    """Check if unit has an active lease."""
    return _get_active_lease(unit) is not None


@register.filter
def get_lease(unit):
    """Safely get unit’s active lease or return None."""
    return _get_active_lease(unit)


@register.filter
def has_tenant(unit):
    """Check if unit has an active lease with a tenant."""
    lease = _get_active_lease(unit)
    return lease is not None and lease.tenant is not None


@register.filter
def get_tenant(unit):
    """Safely get tenant from the active lease or return None."""
    lease = _get_active_lease(unit)
    return lease.tenant if lease and lease.tenant else None


@register.filter
def get_tenant_name(unit):
    """Safely get tenant name from the active lease or return empty string."""
    tenant = get_tenant(unit)
    return tenant.full_name if tenant else ""


@register.filter
def get_lease_start_date(unit):
    """Safely get lease start date from the active lease or return None."""
    lease = _get_active_lease(unit)
    return lease.start_date if lease else None
