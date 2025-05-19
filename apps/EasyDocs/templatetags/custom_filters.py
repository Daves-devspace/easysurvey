from django import template

register = template.Library()



@register.filter
def mul(value, arg):
    try:
        return float(value) * float(arg)
    except (ValueError, TypeError):
        return 0


@register.filter
def dict_get(d, key):
    return d.get(key)


@register.filter
def get_item(dictionary, key):
    """
    Return dictionary[key] or [] if missing.
    Usage: mydict|get_item:some_key
    """
    try:
        return dictionary.get(key, [])
    except Exception:
        return []
