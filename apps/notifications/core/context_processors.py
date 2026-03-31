from apps.notifications.firebase_manager import get_active_config
import logging

logger = logging.getLogger(__name__)

def firebase_config(request):
    """
    Exposes Firebase public config to all templates.
    Fetches credentials from the database (FirebaseConfig model) 
    instead of settings.py.
    """
    # 1. Fetch the active configuration from the DB using our manager helper
    keys = [
        'apiKey', 'authDomain', 'projectId', 'storageBucket',
        'messagingSenderId', 'appId', 'vapidKey'
    ]
    empty_config = {k: '' for k in keys}
    try:
        config_model = get_active_config()
    except Exception:
        logger.exception("Failed to load Firebase config; returning empty template config.")
        return {'firebase_config': empty_config}

    if not config_model:
        return {'firebase_config': empty_config}

    try:
        config_dict = {
            'apiKey': getattr(config_model, 'api_key', ''),
            'authDomain': getattr(config_model, 'auth_domain', ''),
            'projectId': getattr(config_model, 'project_id', ''),
            'storageBucket': getattr(config_model, 'storage_bucket', ''),
            'messagingSenderId': getattr(config_model, 'messaging_sender_id', ''),
            'appId': getattr(config_model, 'app_id', ''),
            'vapidKey': getattr(config_model, 'vapid_key', ''),
        }
        # Ensure all keys are present
        for k in keys:
            config_dict.setdefault(k, '')
        return {'firebase_config': config_dict}
    except Exception:
        logger.exception("Failed to build Firebase template config; returning empty config.")
        return {'firebase_config': empty_config}
    
    return {
        'firebase_config': config_dict
    }