# apps/tenant_management/utils.py
from django.db.models import Exists, OuterRef, Q
from apps.tenant_management.models import Unit, Lease

def filter_units_for_property(property_obj, status=None):
    """
    Return a queryset of Units for the given property_obj, annotated with
    `has_active_lease` boolean which is True when there's an active Lease.
    status: None | 'all' | 'occupied' | 'vacant'
    """
    # Base qs for the property
    qs = Unit.objects.filter(property=property_obj)

    # Annotate with whether an active lease exists for that unit
    active_lease_qs = Lease.objects.filter(unit=OuterRef('pk'), is_active=True)
    qs = qs.annotate(has_active_lease=Exists(active_lease_qs))

    # Do the filtering
    if status is None or status == 'all':
        filtered = qs
    elif status == 'occupied':
        filtered = qs.filter(has_active_lease=True)
    elif status == 'vacant':
        filtered = qs.filter(has_active_lease=False)
    else:
        # unknown status -> return all (or raise ValueError if you prefer)
        filtered = qs

    # order and prefetch/select_related as needed
    # If you have a tenant FK through lease, you might want to prefetch that.
    # Keep select_related minimal; leases are reverse relations so use prefetch if needed.
    return filtered.order_by('unit_number')
