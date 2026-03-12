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


@register.filter
def person_name(user):
    """
    Return a person's full name if available, else username, else '-'.
    """
    if not user:
        return "-"

    full_name = ""
    try:
        full_name = (user.get_full_name() or "").strip()
    except Exception:
        full_name = ""

    if full_name:
        return full_name

    username = getattr(user, "username", "")
    return username or "-"


@register.filter
def first_initial_role(user):
    """
    Format as: "FirstName L-Role" (e.g. "David M-admin").
    Falls back safely when name/profile data is missing.
    """
    if not user:
        return "-"

    first = (getattr(user, "first_name", "") or "").strip()
    last = (getattr(user, "last_name", "") or "").strip()
    username = (getattr(user, "username", "") or "").strip()

    if not first:
        first = username or "User"

    name_part = f"{first} {last[:1]}".strip() if last else first

    role_label = "user"
    try:
        profile = getattr(user, "employeeprofile", None)
        if profile and getattr(profile, "role", None):
            role_label = profile.get_role_display().lower()
    except Exception:
        role_label = "user"

    return f"{name_part}-{role_label}"


@register.filter
def person_name_role(user):
    """
    Format as: "Full Name-role" (e.g. "David Mwangi-admin").
    Falls back to username-role when full name is unavailable.
    """
    if not user:
        return "-"

    name_part = person_name(user)

    role_label = "user"
    try:
        profile = getattr(user, "employeeprofile", None)
        if profile and getattr(profile, "role", None):
            role_label = profile.get_role_display().lower()
    except Exception:
        role_label = "user"

    return f"{name_part}-{role_label}"
