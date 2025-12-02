# apps/EasyDocs/files/utils.py  (relevant parts)
import json
import logging
from datetime import timezone, datetime
from django.core.cache import cache
from django.conf import settings
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
import io
import os
from django.db.models import Q
from django.core.files.storage import default_storage   
from google.auth.exceptions import RefreshError
from apps.EasyDocs.files.security import credential_service
from apps.EasyDocs.models import SiteSettings, DriveOAuthToken
from django.utils import timezone as django_timezone
from django.db import transaction

logger = logging.getLogger(__name__)

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email"
]


# --- DriveAdapter: unifies the API used by UnifiedStorage ---
class DriveAdapter:
    def __init__(self, service, root_folder_id=None, source='service_account', credentials=None):
        self.service = service
        self.root_folder_id = root_folder_id
        self.source = source
        self.credentials = credentials
        self._folder_cache = {}  # Add cache

    def _ensure_folder(self, folder_name: str, parent_id: str) -> str:
        """Ensure a folder exists, return its ID"""
        cache_key = (folder_name, parent_id)
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]

        try:
            # Search for existing folder
            query = (
                f"name = '{folder_name}' and "
                f"mimeType = 'application/vnd.google-apps.folder' and "
                f"'{parent_id}' in parents and trashed = false"
            )
            results = self.service.files().list(
                q=query, 
                spaces="drive", 
                fields="files(id, name)", 
                pageSize=1
            ).execute()
            
            items = results.get("files", [])
            if items:
                folder_id = items[0]["id"]
                logger.info(f"📂 Found existing folder '{folder_name}' (ID: {folder_id})")
            else:
                # Create new folder
                file_metadata = {
                    "name": folder_name,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [parent_id]
                }
                folder = self.service.files().create(
                    body=file_metadata, 
                    fields="id, name"
                ).execute()
                folder_id = folder["id"]
                logger.info(f"📂 Created new folder '{folder_name}' (ID: {folder_id})")
            
            self._folder_cache[cache_key] = folder_id
            return folder_id
            
        except Exception as e:
            logger.error(f"Failed to ensure folder '{folder_name}': {e}")
            raise

    def _save(self, name, content):
        """Save file with folder structure"""
        try:
            if hasattr(content, 'seek'):
                content.seek(0)
            
            # Parse path
            parts = name.strip("/").split("/")
            filename = parts[-1]
            folders = parts[:-1]
            
            # Create folder structure
            parent_id = self.root_folder_id
            for folder_name in folders:
                parent_id = self._ensure_folder(folder_name, parent_id)
            
            # Upload file
            media = MediaIoBaseUpload(
                io.BytesIO(content.read()) if not isinstance(content, bytes) else io.BytesIO(content),
                mimetype='application/octet-stream',
                resumable=True
            )

            metadata = {
                'name': filename,
                'parents': [parent_id]
            }

            created = self.service.files().create(
                body=metadata,
                media_body=media,
                fields='id'
            ).execute()
            
            logger.info(f"✅ File '{filename}' uploaded to folder {parent_id}")
            return created.get('id')
            
        except HttpError as e:
            logger.error("Google Drive upload HttpError: %s", e)
            raise
        except Exception as e:
            logger.error("Google Drive upload failed: %s", e)
            raise
        

    def _open(self, file_id):
        try:
            request = self.service.files().get_media(fileId=file_id)
            buf = io.BytesIO()
            downloader = MediaIoBaseDownload(buf, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
            buf.seek(0)
            return buf
        except Exception as e:
            logger.error("Google Drive download failed: %s", e)
            raise

    def delete(self, file_id):
        try:
            self.service.files().delete(fileId=file_id).execute()
            return True
        except Exception as e:
            logger.error("Google Drive delete failed: %s", e)
            return False

    def exists(self, file_id):
        try:
            self.service.files().get(fileId=file_id, fields='id').execute()
            return True
        except HttpError as e:
            # 404-like outcome
            return False
        except Exception as e:
            logger.warning("Drive exists check failed: %s", e)
            return False

    def url(self, file_id):
        # return drive "view" URL
        return f"https://drive.google.com/file/d/{file_id}/view"


# helpers to build services
def _build_service_from_service_account(key_data, delegate_to=None):
    creds = service_account.Credentials.from_service_account_info(key_data, scopes=DRIVE_SCOPES)
    if delegate_to:
        creds = creds.with_subject(delegate_to)
    service = build('drive', 'v3', credentials=creds, cache_discovery=False)
    return service



def _build_service_from_oauth():
    """
    Build a Google Drive service using the single shared company OAuth token.
    Improved error handling for persisting refreshed tokens.
    """
    oauth_cfg = get_oauth_client_config()
    if not oauth_cfg:
        logger.error("OAuth client configuration missing in SiteSettings")
        raise ValueError("OAuth client configuration missing in SiteSettings")

    token_obj = DriveOAuthToken.objects.first()
    if not token_obj:
        logger.error("No DriveOAuthToken found (company token missing)")
        raise ValueError("No company OAuth token found; authorize first")

    if getattr(token_obj, "needs_reauth", False) or not token_obj.refresh_token_encrypted:
        logger.warning(
            "Company OAuth token is missing or marked for re-auth (needs_reauth=%s).",
            getattr(token_obj, "needs_reauth", False),
        )
        try:
            token_obj.needs_reauth = True
            token_obj.save(update_fields=["needs_reauth"])
        except Exception:
            logger.exception("Failed to mark DriveOAuthToken as needs_reauth")
        raise ValueError("Company OAuth token missing or requires re-authorization")

    # Decrypt tokens
    try:
        refresh_token = credential_service.decrypt(token_obj.refresh_token_encrypted)
        access_token = (
            credential_service.decrypt(token_obj.access_token_encrypted)
            if token_obj.access_token_encrypted
            else None
        )
    except Exception as e:
        logger.exception("Failed to decrypt OAuth tokens: %s", e)
        # Mark for reauth to avoid looping behavior (but don't clear refresh token here)
        try:
            token_obj.needs_reauth = True
            token_obj.save(update_fields=["needs_reauth"])
        except Exception:
            logger.exception("Failed to mark DriveOAuthToken as needs_reauth after decrypt failure")
        raise ValueError(f"Failed to decrypt stored OAuth tokens; re-authorization required ({e})") from e

    # Prepare creds (expiry -> naive UTC)
    expiry = token_obj.token_expiry
    if expiry and expiry.tzinfo is not None:
        expiry = expiry.astimezone(timezone.utc).replace(tzinfo=None)

    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=oauth_cfg["client_id"],
        client_secret=oauth_cfg["client_secret"],
        scopes=DRIVE_SCOPES,
        expiry=expiry,
    )

    # Refresh if needed
    if not creds.valid and creds.refresh_token:
        try:
            logger.info("Refreshing company OAuth credentials...")
            creds.refresh(Request())

            # ENCRYPT first, so we catch encryption errors before touching DB
            try:
                encrypted_access = credential_service.encrypt(creds.token) if creds.token else None
            except Exception as e_enc:
                logger.exception("Failed to encrypt refreshed access token: %s", e_enc)
                # Do not clear the refresh token: encryption failure likely environment/key problem.
                # Update SiteSettings to surface the problem and raise an informative error.
                try:
                    site_settings = SiteSettings.objects.first()
                    if site_settings:
                        site_settings.drive_config_status = "error"
                        site_settings.drive_last_test_status = f"Encryption failed when saving refreshed token: {e_enc}"
                        site_settings.save(update_fields=["drive_config_status", "drive_last_test_status"])
                except Exception:
                    logger.exception("Failed to update SiteSettings after encryption failure")

                raise ValueError(f"Failed to encrypt refreshed access token: {e_enc}") from e_enc

            # Now persist the encrypted token and expiry
            try:
                # Ensure token_expiry stored as aware UTC or None
                expiry_to_store = None
                if creds.expiry:
                    # convert to aware UTC if needed
                    exp = creds.expiry
                    if exp.tzinfo is None:
                        # treat as UTC naive
                        expiry_to_store = django_timezone.make_aware(exp, timezone=timezone.utc)
                    else:
                        expiry_to_store = exp.astimezone(django_timezone.utc)

                token_obj.access_token_encrypted = encrypted_access
                token_obj.token_expiry = expiry_to_store
                token_obj.needs_reauth = False
                token_obj.save(update_fields=["access_token_encrypted", "token_expiry", "needs_reauth"])
                logger.info("Company OAuth token refreshed and persisted successfully")
            except Exception as e_save:
                logger.exception("Failed to persist refreshed OAuth token to DB: %s", e_save)
                # If persisting fails, attempt to set SiteSettings and mark needs_reauth to avoid loops
                try:
                    with transaction.atomic():
                        token_obj.needs_reauth = True
                        token_obj.save(update_fields=["needs_reauth"])
                except Exception:
                    logger.exception("Failed to mark token_obj.needs_reauth after DB persist failure")

                try:
                    site_settings = SiteSettings.objects.first()
                    if site_settings:
                        site_settings.drive_config_status = "error"
                        site_settings.drive_last_test_status = f"Failed to persist refreshed OAuth token: {e_save}"
                        site_settings.save(update_fields=["drive_config_status", "drive_last_test_status"])
                except Exception:
                    logger.exception("Failed to update SiteSettings after DB persist failure")

                # Provide the inner exception message for quicker debugging upstream
                raise ValueError(f"Failed to persist refreshed OAuth token: {e_save}") from e_save

        except RefreshError as e:
            # Refresh token invalid/expired/revoked: clear credentials and mark reauth
            logger.error("OAuth refresh failed: %s", e)
            try:
                with transaction.atomic():
                    token_obj.refresh_token_encrypted = None
                    token_obj.access_token_encrypted = None
                    token_obj.token_expiry = None
                    token_obj.scopes = ""
                    token_obj.needs_reauth = True
                    token_obj.save(
                        update_fields=[
                            "refresh_token_encrypted",
                            "access_token_encrypted",
                            "token_expiry",
                            "scopes",
                            "needs_reauth",
                        ]
                    )
                    logger.info("Cleared invalid OAuth tokens and marked needs_reauth=True")
            except Exception:
                logger.exception("Failed to clear invalid OAuth tokens from DB after RefreshError")

            try:
                site_settings = SiteSettings.objects.first()
                if site_settings:
                    site_settings.drive_config_status = "error"
                    site_settings.drive_last_test_status = "Company OAuth token expired or revoked; re-authorization required"
                    site_settings.save(update_fields=["drive_config_status", "drive_last_test_status"])
                    logger.info("Updated SiteSettings with OAuth error status")
            except Exception:
                logger.exception("Failed to update SiteSettings with OAuth error status")

            raise ValueError("Company OAuth token expired or revoked; re-authorization required") from e

        except Exception as e:
            logger.exception("Unexpected error while refreshing/persisting OAuth token: %s", e)
            raise ValueError(f"Failed to persist refreshed OAuth token: {e}") from e

    # Build the Drive service (will work if creds valid or refresh succeeded)
    try:
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        logger.debug("Built Google Drive service client successfully")
        return service, creds
    except Exception as e:
        logger.exception("Failed to build Google Drive service client: %s", e)
        raise ValueError("Failed to build Google Drive service client") from e


def get_drive_storage():
    """
    Return DriveAdapter (or None). Prefer Service Account (Shared Drive) then OAuth fallback.
    """
    from datetime import timezone
    
    cache_key = "drive_adapter_v1"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    site_settings = SiteSettings.objects.first()

    # 1) Try service account if provided
    if site_settings and site_settings.google_drive_service_account_key_encrypted:
        try:
            dec = credential_service.decrypt_service_account_key(site_settings.google_drive_service_account_key_encrypted)
            key_data = json.loads(dec)
            service = _build_service_from_service_account(key_data)
            adapter = DriveAdapter(
                service=service,
                root_folder_id=site_settings.google_drive_root_folder_id,
                source='service_account'
            )
            cache.set(cache_key, adapter, 3600)
            logger.info("DriveAdapter created from service account")
            return adapter
        except Exception as e:
            logger.warning("Service account Drive init failed: %s", e)

    # 2) OAuth fallback
    try:
        token_row = DriveOAuthToken.objects.order_by('-created_at').first()
        if token_row and token_row.refresh_token_encrypted:
            oauth_cfg = get_oauth_client_config()
            if not oauth_cfg:
                logger.warning("OAuth client config missing in DB; cannot use OAuth fallback")
            else:
                refresh_token = credential_service.decrypt(token_row.refresh_token_encrypted)
                access_token = credential_service.decrypt(token_row.access_token_encrypted) if token_row.access_token_encrypted else None

                # FIX: Ensure token_expiry is timezone-aware
                token_expiry = token_row.token_expiry
                if token_expiry and token_expiry.tzinfo is None:
                    logger.debug("Converting naive token_expiry to UTC in get_drive_storage")
                    token_expiry = token_expiry.replace(tzinfo=timezone.utc)

                service, creds = _build_service_from_oauth()

                root_folder = site_settings.google_drive_root_folder_id if site_settings else None
                adapter = DriveAdapter(
                    service=service,
                    root_folder_id=root_folder,
                    source='oauth',
                    credentials=creds
                )
                cache.set(cache_key, adapter, 3600)
                logger.info("DriveAdapter created from OAuth token")
                return adapter
    except Exception as e:
        logger.exception("OAuth Drive init failed: %s", e)  # Changed to logger.exception to see full traceback

    cache.set(cache_key, None, 60)
    logger.info("No Drive adapter available")
    return None


def get_default_storage_backend(prefer_drive=True):
    """
    Returns a tuple: (storage_instance, backend_name)
    """
    drive_storage = get_drive_storage()

    if prefer_drive and drive_storage:
        return drive_storage, "drive"

    return default_storage, "local"




def get_connection_status(site_settings):
    """
    Return a stable dict describing Drive connection for the shared company OAuth token.
    Includes expired/revoked token detection and explicit needs_reauth flag.
    """
    result = {
        "status": "not_configured",
        "message": "Site settings not configured",
        "class": "warning",
        "storage_mode": "none",
        "storage_mode_display": "No Drive configured",
        "quota_issue": False,
        "expired_token": False,
        "needs_reauth": False,   # NEW: authoritative flag for re-authorization required
    }

    if not site_settings:
        return result

    if not site_settings.google_drive_enabled:
        return {
            **result,
            "status": "disabled",
            "message": "Drive disabled",
            "class": "warning",
            "storage_mode": "none",
            "storage_mode_display": "Drive disabled",
        }

    # Service account configured?
    sa_configured = bool(site_settings.google_drive_service_account_key_encrypted)
    # OAuth client configured?
    from apps.EasyDocs.files.utils import get_oauth_client_config
    oauth_cfg = get_oauth_client_config()
    oauth_configured = bool(oauth_cfg)

    # Single shared OAuth token
    token_obj = DriveOAuthToken.objects.first()
    token_exists = bool(token_obj and token_obj.refresh_token_encrypted)

    # Detect expired token (based on expiry field)
    now_utc = datetime.now(timezone.utc)
    if token_exists and token_obj.token_expiry and token_obj.token_expiry < now_utc:
        result["expired_token"] = True

    # Detect explicit needs_reauth (set on RefreshError/decrypt failure)
    if token_obj and getattr(token_obj, "needs_reauth", False):
        result["needs_reauth"] = True

    # Determine storage mode
    if sa_configured:
        result.update({
            "storage_mode": "service_account",
            "storage_mode_display": "Service Account",
            "status": "not_configured",
            "message": "Service Account configured",
            "class": "info",
        })
    elif token_exists and oauth_configured:
        result.update({
            "storage_mode": "oauth_authorized",
            "storage_mode_display": "Authorized Company Account (OAuth)",
            "status": "not_configured",
            "message": "Company OAuth token available",
            "class": "info",
        })
    elif oauth_configured:
        result.update({
            "storage_mode": "oauth_configured",
            "storage_mode_display": "OAuth client configured (needs authorization)",
            "status": "not_configured",
            "message": "Authorize company account",
            "class": "warning",
        })
    else:
        result.update({
            "storage_mode": "none",
            "storage_mode_display": "No Drive credentials configured",
            "status": "not_configured",
            "message": "No service account or OAuth client configured",
            "class": "warning",
        })

    # Prioritize needs_reauth over expired_token in messaging and class
    if result["needs_reauth"]:
        # Use the site's last_test_status if available, else a clear default
        reason = site_settings.drive_last_test_status or "OAuth token invalid or revoked"
        result["message"] = f"{reason} (Re-authorization required)"
        result["class"] = "danger"
        # Ensure expired_token is True to show the same badge logic if you reuse that flag in templates
        result["expired_token"] = True
    elif result["expired_token"]:
        result["message"] += " (OAuth token expired; refresh may be required)"
        if result["class"] != "danger":
            result["class"] = "warning"

    # Respect site_settings known states
    cfg_state = site_settings.drive_config_status
    if cfg_state == "configured":
        result.update({"status": "connected", "message": "Drive configured", "class": "success"})
    elif cfg_state == "testing":
        result.update({"status": "testing", "message": "Testing...", "class": "info"})
    elif cfg_state == "error":
        result.update({"status": "error", "message": site_settings.drive_last_test_status or "Configuration error", "class": "danger"})

    # Quota issues
    last_status = (site_settings.drive_last_test_status or "").lower()
    if "quota" in last_status or "storagequota" in last_status or "storagequotaexceeded" in last_status:
        result["quota_issue"] = True
        result["message"] = site_settings.drive_last_test_status or result["message"]
        result["class"] = "danger"

    return result




def ensure_root_folder_exists(storage, site_settings, request_user=None):
    """Create or verify the root folder and share with company email"""
    root_folder_name = site_settings.company_name or "EasyDocs"
    
    # If we already have a folder ID, verify it exists
    if site_settings.google_drive_root_folder_id:
        try:
            folder = storage.service.files().get(
                fileId=site_settings.google_drive_root_folder_id,
                fields='id,name,permissions'
            ).execute()
            logger.info(f"✅ Root folder exists: {folder.get('name')}")
            
            # Ensure company email has access
            if site_settings.company_email:
                ensure_folder_sharing(storage, site_settings.google_drive_root_folder_id, site_settings)
            
            return site_settings.google_drive_root_folder_id
            
        except Exception as e:
            logger.warning(f"Root folder not found, creating new one: {e}")
    
    # Create new root folder
    try:
        file_metadata = {
            'name': root_folder_name,
            'mimeType': 'application/vnd.google-apps.folder',
            'description': 'EasyDocs System Root Folder'
        }
        folder = storage.service.files().create(body=file_metadata, fields='id,name').execute()
        folder_id = folder.get('id')
        logger.info(f"✅ Created root folder: {folder.get('name')} (ID: {folder_id})")
        
        # Share with company email and current user
        ensure_folder_sharing(storage, folder_id, site_settings, request_user)
        
        return folder_id
        
    except Exception as e:
        logger.error(f"Failed to create root folder: {e}")
        return None

def ensure_folder_sharing(storage, folder_id, site_settings, request_user=None):
    """Ensure folder is shared with company email and relevant users"""
    try:
        # Always share with company email if available
        if site_settings.company_email:
            share_folder_with_email(
                storage, 
                folder_id, 
                site_settings.company_email, 
                'writer'  # Can view, edit, and share
            )
            logger.info(f"✅ Shared folder with company email: {site_settings.company_email}")
        
        # Share with current user (if provided and different from company email)
        if (request_user and 
            request_user.email and 
            request_user.email != site_settings.company_email):
            
            share_folder_with_email(
                storage,
                folder_id,
                request_user.email,
                'writer'
            )
            logger.info(f"✅ Shared folder with current user: {request_user.email}")
        
        # Share with all superusers and ADMIN group members
        from django.contrib.auth.models import User
        admin_users = User.objects.filter(
            Q(is_superuser=True) | Q(groups__name='ADMIN')
        ).exclude(
            Q(email=site_settings.company_email) | 
            Q(email=request_user.email if request_user else None)
        ).distinct()
        
        for user in admin_users:
            if user.email:
                share_folder_with_email(
                    storage,
                    folder_id,
                    user.email,
                    'writer'
                )
                logger.info(f"✅ Shared folder with admin: {user.email}")
                
    except Exception as e:
        logger.error(f"Failed to set up folder sharing: {e}")

def share_folder_with_email(storage, folder_id, email, role='writer'):
    """Share a folder with a specific email address"""
    try:
        # Check if already shared
        permissions = storage.service.permissions().list(
            fileId=folder_id,
            fields='permissions(id,emailAddress,role)'
        ).execute()
        
        # Check if email already has access
        for perm in permissions.get('permissions', []):
            if perm.get('emailAddress') == email:
                logger.info(f"✅ {email} already has {perm.get('role')} access")
                return
        
        # Share with email
        permission = {
            'type': 'user',
            'role': role,
            'emailAddress': email
        }
        
        storage.service.permissions().create(
            fileId=folder_id,
            body=permission,
            sendNotificationEmail=False  # Don't spam users
        ).execute()
        
        logger.info(f"✅ Shared folder with {email} as {role}")
        
    except Exception as e:
        logger.error(f"Failed to share folder with {email}: {e}")


# def ensure_root_folder_exists(storage, site_settings):
#     """Create or verify the root folder for EasyDocs"""
#     root_folder_name = site_settings.company_name or "EasyDocs"
    
#     # If we already have a folder ID, verify it exists
#     if site_settings.google_drive_root_folder_id:
#         try:
#             folder = storage.service.files().get(
#                 fileId=site_settings.google_drive_root_folder_id,
#                 fields='id,name'
#             ).execute()
#             logger.info(f"✅ Root folder exists: {folder.get('name')}")
#             return site_settings.google_drive_root_folder_id
#         except Exception as e:
#             logger.warning(f"Root folder not found, creating new one: {e}")
    
#     # Create new root folder
#     try:
#         file_metadata = {
#             'name': root_folder_name,
#             'mimeType': 'application/vnd.google-apps.folder',
#             'description': 'EasyDocs System Root Folder'
#         }
#         folder = storage.service.files().create(body=file_metadata, fields='id,name').execute()
#         folder_id = folder.get('id')
#         logger.info(f"✅ Created root folder: {folder.get('name')} (ID: {folder_id})")
#         return folder_id
#     except Exception as e:
#         logger.error(f"Failed to create root folder: {e}")
#         return None

def log_audit(user, action, instance, request=None, extra=None):
    """Log audit trail"""
    from apps.EasyDocs.models import AuditLog
    
    try:
        AuditLog.objects.create(
            user=user if user and user.is_authenticated else None,
            action=action,
            model_name=instance.__class__.__name__,
            object_id=instance.pk,
            description=extra,
            ip_address=request.META.get('REMOTE_ADDR') if request else None,
            user_agent=request.META.get('HTTP_USER_AGENT') if request else None,
        )
    except Exception as e:
        logger.error(f"Failed to log audit: {e}")

def sync_document_to_drive(document):
    """Sync a document to Google Drive if enabled"""
    if not document.site_settings or not document.site_settings.is_google_drive_ready():
        return False
    
    try:
        return document.ensure_drive_copy()
    except Exception as e:
        logger.error(f"Failed to sync document {document.id} to Drive: {e}")
        return False

def get_document_url(document):
    """Get the best available URL for a document"""
    if document.drive_url and document.storage_backend in ['drive', 'hybrid']:
        return document.drive_url
    elif document.doc_file:
        return document.doc_file.url
    return None






def load_drive_credentials():
    """
    Load Google Drive credentials from settings.
    This is used for OAuth flow (if implemented).
    """
    try:
        # For OAuth flow, you might have a credentials file path in settings
        credentials_path = getattr(settings, 'GOOGLE_DRIVE_CREDENTIALS_FILE', None)
        if credentials_path:
            with open(credentials_path, 'r') as f:
                return json.load(f)
        
        # Alternatively, check if we have service account configuration
        from apps.EasyDocs.models import SiteSettings
        site_settings = SiteSettings.objects.first()
        if site_settings and site_settings.google_drive_service_account_key_encrypted:
            # Decrypt and return service account credentials
            service_account_json = credential_service.decrypt_service_account_key(
                site_settings.google_drive_service_account_key_encrypted
            )
            return json.loads(service_account_json)
            
        return None
    except Exception as e:
        logger.error(f"Failed to load Drive credentials: {e}")
        return None
    



from urllib.parse import urlparse
from typing import Dict, Any
from django.urls import reverse
def validate_redirect_uri(uri: str, allowed_uris: list[str]) -> bool:
    """
    Validate the given redirect_uri against allowed URIs.
    Returns True if valid, False otherwise.
    """
    parsed_uri = urlparse(uri)
    norm_path = parsed_uri.path.rstrip("/")

    for allowed in allowed_uris:
        parsed_allowed = urlparse(allowed)
        allowed_path = parsed_allowed.path.rstrip("/")

        if (parsed_uri.scheme == parsed_allowed.scheme and
            parsed_uri.hostname == parsed_allowed.hostname and
            (parsed_uri.port or 80) == (parsed_allowed.port or 80) and
            norm_path == allowed_path):
            return True

    return False


    

    
from typing import List, Tuple
from urllib.parse import urlparse, urlunparse

def _generate_candidate_variants(uri: str) -> List[str]:
    """
    Generate likely variants of a redirect URI (trailing slash, ports, 127.0.0.1 alias, http/https).
    These are only suggestions for developer convenience.
    """
    if not uri:
        return []
    p = urlparse(uri)
    scheme = p.scheme or "http"
    host = p.hostname or ""
    port = p.port
    path = p.path or "/"
    candidates = set()

    def build(s, h, prt, pth):
        netloc = f"{h}:{prt}" if prt else h
        return urlunparse((s, netloc, pth, "", "", ""))

    # original and trailing slash variants
    candidates.add(build(scheme, host, port, path))
    candidates.add(build(scheme, host, port, path.rstrip("/") or "/"))
    candidates.add(build(scheme, host, port, (path.rstrip("/") or "/") + "/"))

    # localhost / 127.0.0.1 variations with common dev ports
    if host in ("localhost", "127.0.0.1"):
        alt_hosts = ("localhost", "127.0.0.1")
        if port:
            for alt in alt_hosts:
                candidates.add(build(scheme, alt, port, path))
        else:
            for alt in alt_hosts:
                for try_port in (8000, 8080, 80):
                    candidates.add(build(scheme, alt, try_port, path))

    # alternate scheme
    alt_scheme = "https" if scheme == "http" else "http"
    candidates.add(build(alt_scheme, host, port, path))

    return sorted(candidates)



def _normalize_uri(uri: str) -> str:
    """
    Normalize URI for comparison:
    - Lowercase scheme and hostname
    - Include port (explicit or default)
    - Remove trailing slash from path
    - Remove query and fragment
    """
    if not uri:
        return ""
    
    p = urlparse(uri)
    scheme = (p.scheme or "http").lower()
    host = (p.hostname or "").lower()
    
    # Handle port: use explicit port or default based on scheme
    if p.port:
        port = p.port
    else:
        port = 443 if scheme == "https" else 80
    
    path = (p.path or "/").rstrip("/")
    
    return f"{scheme}://{host}:{port}{path}"


def _build_redirect_uri(request, callback_view_name="drive_oauth_callback") -> str:
    """
    Build redirect_uri using available request/proxy headers.
    """
    from django.urls import reverse
    
    # Determine protocol
    proto = request.META.get("HTTP_X_FORWARDED_PROTO")
    if not proto:
        proto = "https" if request.is_secure() else "http"
    
    # Determine host (with port)
    forwarded_host = request.META.get("HTTP_X_FORWARDED_HOST")
    http_host = request.META.get("HTTP_HOST")
    
    host = forwarded_host or http_host
    
    if not host:
        try:
            host = request.get_host()
        except Exception:
            # Fallback to building from request
            return request.build_absolute_uri(reverse(callback_view_name))
    
    # Build the path
    path = reverse(callback_view_name)
    if not path.startswith("/"):
        path = "/" + path
    
    # Ensure trailing slash matches your URL pattern
    if not path.endswith("/"):
        path += "/"
    
    return f"{proto}://{host}{path}"


def pick_and_validate_redirect_uri(request, allowed_uris: List[str]) -> Tuple[str, List[str]]:
    """
    Determine redirect_uri and validate against allowed_uris.
    
    Returns:
        (redirect_uri, diagnostics_list)
        diagnostics_list is empty if valid, contains error messages otherwise
    """
    redirect_uri = _build_redirect_uri(request, "drive_oauth_callback")
    diagnostics: List[str] = []
    
    logger.info(f"Generated redirect_uri: {redirect_uri}")
    logger.info(f"Allowed URIs from settings: {allowed_uris}")
    
    if not allowed_uris:
        diagnostics.append("No allowed redirect URIs configured in SiteSettings.")
        diagnostics.append(f"Add this URI to SiteSettings: {redirect_uri}")
        return redirect_uri, diagnostics
    
    # Normalize for comparison
    norm_redirect = _normalize_uri(redirect_uri)
    logger.info(f"Normalized redirect_uri: {norm_redirect}")
    
    # Check exact match first (case-insensitive)
    for allowed in allowed_uris:
        if redirect_uri.lower() == allowed.lower():
            logger.info(f"✅ Exact match found: {allowed}")
            return redirect_uri, diagnostics
    
    # Check normalized match
    for allowed in allowed_uris:
        norm_allowed = _normalize_uri(allowed)
        logger.info(f"Comparing normalized: {norm_redirect} vs {norm_allowed}")
        
        if norm_redirect == norm_allowed:
            logger.info(f"✅ Normalized match found: {allowed}")
            return redirect_uri, diagnostics
    
    # No match found - provide diagnostics
    diagnostics.append(
        "Redirect URI mismatch. The URI must match exactly what's in Google Cloud Console."
    )
    diagnostics.append(f"Current redirect URI: {redirect_uri}")
    diagnostics.append("Configured allowed URIs:")
    for uri in allowed_uris:
        diagnostics.append(f"  - {uri}")
    
    diagnostics.append("")
    diagnostics.append("Solutions:")
    diagnostics.append(f"1. Add this exact URI to Google Cloud Console: {redirect_uri}")
    diagnostics.append("2. Or update SiteSettings to match what's in Google Cloud Console")
    
    # Generate helpful suggestions
    parsed = urlparse(redirect_uri)
    suggestions = [
        redirect_uri,
        f"{parsed.scheme}://{parsed.hostname}{parsed.path}",  # without port
        redirect_uri.rstrip("/"),  # without trailing slash
        redirect_uri.rstrip("/") + "/",  # with trailing slash
    ]
    
    if parsed.hostname == "localhost":
        suggestions.extend([
            redirect_uri.replace("localhost", "127.0.0.1"),
            redirect_uri.replace("localhost", "127.0.0.1").rstrip("/"),
        ])
    
    diagnostics.append("")
    diagnostics.append("Common variants to try:")
    for suggestion in list(dict.fromkeys(suggestions))[:5]:  # unique, first 5
        diagnostics.append(f"  {suggestion}")
    
    return redirect_uri, diagnostics


def get_oauth_client_config():
    from apps.EasyDocs.models import SiteSettings
    from apps.EasyDocs.files.security import credential_service
    
    site = SiteSettings.objects.first()
    if not site:
        return None
    
    client_id = getattr(site, "google_oauth_client_id", None)
    encrypted_secret = getattr(site, "google_oauth_client_secret_encrypted", None)
    
    if not client_id or not encrypted_secret:
        return None
    
    try:
        client_secret = credential_service.decrypt(encrypted_secret)
        
        # Parse allowed redirect URIs
        allowed_uris_raw = getattr(site, "google_oauth_redirect_uris", "")
        if isinstance(allowed_uris_raw, str):
            allowed_uris = [
                uri.strip() 
                for uri in allowed_uris_raw.replace("\n", ",").split(",") 
                if uri.strip()
            ]
        else:
            allowed_uris = []
        
        return {
            "client_id": client_id,
            "client_secret": client_secret,
            "allowed_redirect_uris": allowed_uris
        }
    except Exception as e:
        logger.exception(f"Failed to get OAuth config: {e}")
        return None