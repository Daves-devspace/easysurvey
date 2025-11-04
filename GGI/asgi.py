"""
ASGI config for GGI project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.1/howto/deployment/asgi/
"""

# GGI/asgi.py
import os

# MUST set settings env var first, before importing anything that might touch Django apps
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "GGI.settings")

from django.core.asgi import get_asgi_application
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter

# Import routing AFTER DJANGO_SETTINGS_MODULE is set and get_asgi_application is available.
# This prevents "Apps aren't loaded yet" errors if routing/consumers import models.
from apps.notifications import routing as notifications_routing

# Ensure Django app registry is initialized (get_asgi_application triggers setup)
django_asgi_app = get_asgi_application()

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AuthMiddlewareStack(
        URLRouter(notifications_routing.websocket_urlpatterns)
    ),
})
