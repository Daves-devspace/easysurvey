from django import template

register = template.Library()

@register.filter
def storage_badge(storage_backend):
    if storage_backend == "local":
        return '<span class="badge bg-success">System</span>'
    elif storage_backend == "drive":
        return '<span class="badge bg-primary">Google Drive</span>'
    elif storage_backend == "hybrid":
        return '<span class="badge bg-warning text-dark">Hybrid</span>'
    return '<span class="badge bg-secondary">Unknown</span>'
