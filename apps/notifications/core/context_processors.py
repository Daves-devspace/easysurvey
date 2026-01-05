from apps.notifications.firebase_manager import get_active_config

def firebase_config(request):
    """
    Exposes Firebase public config to all templates.
    Fetches credentials from the database (FirebaseConfig model) 
    instead of settings.py.
    """
    # 1. Fetch the active configuration from the DB using our manager helper
    config_model = get_active_config()
    
    # 2. Safety check: If no config exists in DB, return empty dict
    # This prevents the site from crashing if you haven't set up the Admin yet.
    if not config_model:
        return {'firebase_config': {}}

    # 3. Construct the Public Configuration Dictionary
    # CRITICAL SECURITY NOTE: We explicitly select ONLY the public fields.
    # We DO NOT include 'service_account_json' here because that is the private key.
    config_dict = {
        'apiKey': config_model.api_key,
        'authDomain': config_model.auth_domain,
        'projectId': config_model.project_id,
        'storageBucket': config_model.storage_bucket,
        'messagingSenderId': config_model.messaging_sender_id,
        'appId': config_model.app_id,
        'vapidKey': config_model.vapid_key,
    }
    
    return {
        'firebase_config': config_dict
    }