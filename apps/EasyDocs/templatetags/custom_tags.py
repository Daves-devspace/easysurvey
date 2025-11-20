from django import template
from datetime import datetime
register = template.Library()

@register.filter
def get_item(dictionary, key):
    return dictionary.get(key, '')





@register.filter
def make_range(start, end):
    """
    Generate a range from start to end (inclusive).
    Usage: {% for y in 2020|make_range:current_year %}
    """
    try:
        start = int(start)
        end = int(end)
        return range(start, end + 1)
    except (ValueError, TypeError):
        return []


@register.filter
def date(value, format_string):
    """
    Format a month number as a month name.
    Usage: {{ 1|date:"F" }} => January
    """
    if isinstance(value, int):
        try:
            # Create a dummy date with the given month
            dt = datetime(2000, value, 1)
            return dt.strftime(format_string)
        except (ValueError, TypeError):
            return value
    return value


@register.filter
def add(value, arg):
    """
    Add the arg to the value.
    Usage: {{ current_year|add:-5 }}
    """
    try:
        return int(value) + int(arg)
    except (ValueError, TypeError):
        return value
