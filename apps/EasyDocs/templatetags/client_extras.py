# templatetags/client_extras.py
from django import template

from apps.EasyDocs.forms import ClientForm

register = template.Library()

@register.simple_tag
def get_client_form(client):
    return ClientForm(instance=client)
