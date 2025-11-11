"""
URL configuration for GGI project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
import os

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.shortcuts import render
from django.urls import path, include
from django.http import HttpResponseNotFound

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('apps.EasyDocs.urls')),
    path('employee/', include('apps.Employee.urls')),
    path('accounts/', include('apps.accounts.urls')),
    path('notifications/', include('apps.notifications.urls')),
]
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    
def ignore_well_known(request, *args, **kwargs):
    # Return 404 silently to stop Chrome/.well-known probes from hitting debug tracebacks
    return HttpResponseNotFound()
# Prevent Chrome's .well-known lookups from cluttering your logs
urlpatterns += [
    path(".well-known/<path:any>/", ignore_well_known),
]


def custom_400(request, exception):
    return render(request, 'errors/400.html', status=400)


def custom_403(request, exception):
    return render(request, 'errors/403.html', status=403)


def custom_404(request, exception):
    return render(request, 'errors/404.html', status=404)


def custom_500(request):
    return render(request, 'errors/500.html', status=500)


handler400 = 'GGI.urls.custom_400'
handler403 = 'GGI.urls.custom_403'
handler404 = 'GGI.urls.custom_404'
handler500 = 'GGI.urls.custom_500'
