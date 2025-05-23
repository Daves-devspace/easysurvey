from django import template

from apps.EasyDocs.forms import ServiceForm

register = template.Library()

@register.simple_tag
def bound_service_form(service):
    return ServiceForm(instance=service)
