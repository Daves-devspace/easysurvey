import logging
import json
import firebase_admin
from firebase_admin import credentials
from .models import FirebaseConfig

logger = logging.getLogger(__name__)

def get_active_config():
    """
    Fetch the active configuration from the DB.
    Returns None if not configured.
    """
    try:
        # Use .first() to get the single active config
        return FirebaseConfig.objects.filter(is_active=True).first()
    except Exception:
        # Failsafe for migrations or early startup (prevents crashing if table doesn't exist)
        return None

def initialize_firebase():
    """
    Initializes the Firebase Admin SDK using the DB config.
    Should be called before sending any notification.
    """
    # 1. Check if already initialized to avoid "App already exists" errors
    if firebase_admin._apps:
        return firebase_admin.get_app()

    # 2. Get Config from DB
    config_model = get_active_config()
    
    if not config_model:
        logger.warning("⚠️ No active FirebaseConfig found in database. Push notifications will fail.")
        return None

    # 3. Initialize with JSON from DB
    try:
        # Parse the JSON string stored in the TextField
        cred_dict = json.loads(config_model.service_account_json)
        cred = credentials.Certificate(cred_dict)
        
        # Initialize the app with the credentials
        app = firebase_admin.initialize_app(cred)
        logger.info(f"✅ Firebase initialized successfully for project: {config_model.project_id}")
        return app
    except Exception as e:
        logger.exception(f"❌ Failed to initialize Firebase from DB config: {e}")
        return None