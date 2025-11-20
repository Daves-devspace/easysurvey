from django.conf import settings

def firebase_config(request):
    return {
        "firebase_config": settings.FIREBASE_CONFIG,
    }
