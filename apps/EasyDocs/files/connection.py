# apps/EasyDocs/views/connection.py

# apps/EasyDocs/views/connection.py
from googleapiclient.errors import HttpError
import json
import logging
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.views.decorators.http import require_POST, require_http_methods
from django.utils import timezone
from django.http import JsonResponse
from django.core.cache import cache
from django.template.loader import render_to_string
from django.conf import settings
from django.urls import reverse
from django.views import View
from apps.EasyDocs.models import SiteSettings
from apps.EasyDocs.forms import GoogleDriveConfigForm
from apps.EasyDocs.files.security import credential_service
from google.oauth2.credentials import Credentials
from apps.EasyDocs.files.utils import get_connection_status, get_drive_storage, ensure_root_folder_exists
from django.contrib.auth.decorators import login_required
from django.utils.timezone import now
from google.oauth2 import service_account
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
logger = logging.getLogger(__name__)

def is_deployment_manager(user):
    return (user.is_authenticated and
            (user.is_superuser or user.groups.filter(name="ADMIN").exists()))



def save_google_drive_settings(site_settings, form, user=None):
    """
    Save/update Google Drive settings from the form.
    - Encrypts OAuth client secret only if a new secret is provided.
    - Encrypts service account key if uploaded.
    - Updates other configuration fields.
    - Clears cached readiness flag.
    """

    # --- OAuth client secret ---
    # Only encrypt and store if user provided a new secret.
    raw_secret = form.cleaned_data.get("google_oauth_client_secret")
    if raw_secret:
        try:
            site_settings.google_oauth_client_secret_encrypted = credential_service.encrypt(raw_secret)
        except Exception as e:
            logger.exception("Failed to encrypt OAuth client secret: %s", e)
            raise ValueError(f"Failed to encrypt OAuth client secret: {e}")

    # --- Service account key ---
    service_account_file = form.cleaned_data.get("service_account_key")
    if service_account_file:
        try:
            content = service_account_file.read().decode("utf-8")
            key_data = json.loads(content)
            site_settings.google_drive_service_account_key_encrypted = credential_service.encrypt_service_account_key(content)
            site_settings.google_drive_service_account_email = key_data.get("client_email")
            site_settings.drive_config_status = "configured"
        except Exception as e:
            logger.exception("Failed to process service account key: %s", e)
            raise ValueError(f"Failed to process service account key: {e}")

    # --- Other fields ---
    site_settings.google_drive_enabled = bool(form.cleaned_data.get("google_drive_enabled"))
    site_settings.google_drive_root_folder_id = form.cleaned_data.get("google_drive_root_folder_id") or ""
    site_settings.google_oauth_client_id = form.cleaned_data.get("google_oauth_client_id") or ""
    #site_settings.google_oauth_client_secret_encrypted = site_settings.google_oauth_client_secret_encrypted  # Retain existing if not updated
    site_settings.drive_auto_folder_creation = bool(form.cleaned_data.get("drive_auto_folder_creation"))
    site_settings.drive_file_naming_pattern = form.cleaned_data.get("drive_file_naming_pattern") or site_settings.drive_file_naming_pattern

    # --- Metadata ---
    if user:
        site_settings.drive_config_updated_by = user
    site_settings.drive_config_updated_at = timezone.now()

    # Save and clear cache
    site_settings.save()
    cache.delete("google_drive_service_ready")

    logger.info("Google Drive settings updated successfully by user=%s", user)


@login_required
def google_drive_deployment_config(request):
    """
    Always returns the Google Drive config partial for both GET and POST.
    If AJAX, returns JSON, otherwise renders the partial inside the management template.
    """
    try:
        site_settings, _ = SiteSettings.objects.get_or_create(pk=1)
    except Exception as e:
        logger.exception("Failed to retrieve SiteSettings: %s", e)
        site_settings = None

    # Prepare the form (POST data if present)
    if request.method == "POST":
        form = GoogleDriveConfigForm(request.POST, request.FILES, instance=site_settings)
        if form.is_valid():
            try:
                save_google_drive_settings(site_settings, form, request.user)
                # Rebuild context with updated form
                ctx = create_template_context(site_settings)
                ctx.update({
                    "gdrive_form": GoogleDriveConfigForm(instance=site_settings),
                    "has_encrypted_key": bool(getattr(site_settings, "google_drive_service_account_key_encrypted", None)),
                    "service_account_email": getattr(site_settings, "google_drive_service_account_email", ""),
                    "connection_status": get_connection_status(site_settings),
                })
                html = render_to_string("Management/partials/_google_drive_tab.html", ctx, request=request)
                return JsonResponse({"success": True, "html": html, "message": "Configuration updated."})
            except Exception as e:
                ctx = create_template_context(site_settings, form)
                html = render_to_string("Management/partials/_google_drive_tab.html", ctx, request=request)
                return JsonResponse({"success": False, "message": str(e), "html": html}, status=400)
        else:
            ctx = create_template_context(site_settings, form)
            html = render_to_string("Management/partials/_google_drive_tab.html", ctx, request=request)
            return JsonResponse({"success": False, "errors": form.errors.get_json_data(), "html": html}, status=400)

    # --- GET request ---
    form = GoogleDriveConfigForm(instance=site_settings)
    ctx = create_template_context(site_settings, form)
    ctx.update({
        "gdrive_form": form,
        "has_encrypted_key": bool(getattr(site_settings, "google_drive_service_account_key_encrypted", None)),
        "service_account_email": getattr(site_settings, "google_drive_service_account_email", ""),
        "connection_status": get_connection_status(site_settings),
    })

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        html = render_to_string("Management/partials/_google_drive_tab.html", ctx, request=request)
        return JsonResponse({"success": True, "html": html})
    else:
        return render(request, "Management/management.html", ctx)



@user_passes_test(is_deployment_manager)
@require_POST
def google_drive_config_update_ajax(request):
    try:
        site_settings, _ = SiteSettings.objects.get_or_create(pk=1)

        form = GoogleDriveConfigForm(request.POST, request.FILES, instance=site_settings)

        if not form.is_valid():
            ctx = create_template_context(site_settings, form)
            # Ensure these keys are always present
            ctx.update({
                "has_encrypted_key": bool(getattr(site_settings, "google_drive_service_account_key_encrypted", None)),
                "service_account_email": getattr(site_settings, "google_drive_service_account_email", ""),
                "connection_status": get_connection_status(site_settings),
            })
            html = render_to_string("Management/partials/_google_drive_tab.html", ctx, request=request)
            return JsonResponse({"success": False, "errors": form.errors.get_json_data(), "html": html}, status=400)

        save_google_drive_settings(site_settings, form, request.user)
        

        # Rebuild context for partial to reflect latest saved values
        ctx = create_template_context(site_settings)
        ctx.update({
            "gdrive_form": GoogleDriveConfigForm(instance=site_settings),  # Updated form with saved values
            "has_encrypted_key": bool(getattr(site_settings, "google_drive_service_account_key_encrypted", None)),
            "service_account_email": getattr(site_settings, "google_drive_service_account_email", ""),
            "connection_status": get_connection_status(site_settings),
        })
        html = render_to_string("Management/partials/_google_drive_tab.html", ctx, request=request)

        return JsonResponse({
            "success": True,
            "message": "Google Drive configuration updated successfully.",
            "html": html,
        })

    except Exception as e:
        logger.exception("Unexpected error updating Google Drive configuration: %s", e)
        return JsonResponse({"success": False, "message": str(e), "html": ""}, status=500)

    

@user_passes_test(is_deployment_manager)
@require_POST
def google_drive_config_clear_key(request):
    """Clears the encrypted service account key."""
    try:
        site_settings = SiteSettings.objects.first() or SiteSettings.objects.create()

        site_settings.google_drive_service_account_key_encrypted = None
        site_settings.google_drive_service_account_email = None
        site_settings.drive_config_status = "not_configured"
        site_settings.drive_config_updated_by = request.user
        site_settings.save()
        cache.delete("google_drive_service_ready")

        context = create_template_context(site_settings)
        html = render_to_string("Management/partials/_google_drive_tab.html", context, request=request)

        return JsonResponse({
            "success": True,
            "message": "Service account key cleared",
            "has_encrypted_key": False,
            "service_account_email": None,
            "connection_status": get_connection_status(site_settings),
            "html": html,
        })

    except Exception as e:
        logger.exception("Failed to clear google drive key: %s", e)
        return JsonResponse({"success": False, "error": str(e), "html": ""}, status=500)





# -------------------------------------------------------------------
# Utility: centralize error response creation
# -------------------------------------------------------------------
def create_success_response(*, site_settings, message, payload=None):
    """
    Wrap success responses with consistent logging, DB updates,
    and optional payload enrichment.
    """
    site_settings.drive_last_test_status = f"SUCCESS: {message}"
    site_settings.drive_last_test_at = now()
    site_settings.drive_config_status = "configured"
    site_settings.save(update_fields=["drive_last_test_status", "drive_last_test_at", "drive_config_status"])

    logger.info("Google Drive test succeeded: %s", message)

    response_data = {
        "success": True,
        "message": message,
        "steps": ["load config", "authenticate", "connect to drive", "validate root folder"],
    }
    if payload:
        response_data.update(payload)

    return JsonResponse(response_data, status=200)


def create_error_response(*, site_settings, message, error=None, step="unknown"):
    """
    Wrap error responses with consistent logging, DB updates,
    and attach step info automatically.
    """
    site_settings.drive_last_test_status = f"ERROR: {message}"
    site_settings.drive_last_test_at = now()
    site_settings.drive_config_status = "error"
    site_settings.save(update_fields=["drive_last_test_status", "drive_last_test_at", "drive_config_status"])

    if error:
        logger.exception("Google Drive test failed at step=%s: %s", step, message, exc_info=error)
    else:
        logger.error("Google Drive test failed at step=%s: %s", step, message)

    return JsonResponse({
        "success": False,
        "message": message,
        "step": step,
        "steps": ["load config", "authenticate", "connect to drive", "validate root folder"],
        "error": str(error) if error else None,
    }, status=400)

# -------------------------------------------------------------------
# Template context builder (single unified version)
# -------------------------------------------------------------------
def create_template_context(site_settings, form=None):
    """
    Builds a context dictionary for Google Drive tab templates.
    
    Includes all required fields for form rendering and status display.
    If a form instance is not provided, creates a default form bound to SiteSettings.
    """
    if form is None:
        form = GoogleDriveConfigForm(instance=site_settings)

    return {
        "site_settings": site_settings,
        "gdrive_form": form,
        "company_email": site_settings.company_email,
        "drive_enabled": site_settings.google_drive_enabled,
        "drive_status": site_settings.drive_config_status,
        "drive_last_test_status": site_settings.drive_last_test_status,
        "drive_last_test_at": site_settings.drive_last_test_at,
        "drive_root_folder_id": site_settings.google_drive_root_folder_id,
        "has_encrypted_key": bool(site_settings.google_drive_service_account_key_encrypted),
        "service_account_email": site_settings.google_drive_service_account_email,
        # ✅ Add connection_status
        "connection_status": get_connection_status(site_settings),
    }


# -------------------------------------------------------------------
# Debug Drive config (single unified version)
# -------------------------------------------------------------------
def debug_drive_config(site_settings):
    """Return decrypted config for debugging (do not expose secrets to UI)."""
    try:
        decrypted_key = credential_service.decrypt_service_account_key(
            site_settings.google_drive_service_account_key_encrypted
        ) if site_settings.google_drive_service_account_key_encrypted else None
    except Exception as e:
        logger.exception("Failed to decrypt service account key: %s", e)
        decrypted_key = None

    return {
        "enabled": site_settings.google_drive_enabled,
        "root_folder_id": site_settings.google_drive_root_folder_id,
        "service_account_email": site_settings.google_drive_service_account_email,
        "key_present": bool(site_settings.google_drive_service_account_key_encrypted),
        "key_decrypted_ok": decrypted_key is not None,
    }


# -------------------------------------------------------------------
# Emergency share utility
# -------------------------------------------------------------------
def emergency_share_folder(storage, folder_id, site_settings):
    """Share a folder with company email if urgently needed."""
    target_email = site_settings.company_email
    if not target_email:
        logger.warning("No company email configured, cannot emergency-share folder %s", folder_id)
        return None

    try:
        perm = storage.service.permissions().create(
            fileId=folder_id,
            body={"type": "user", "role": "writer", "emailAddress": target_email},
            sendNotificationEmail=False
        ).execute()
        logger.info("Emergency shared folder %s with %s", folder_id, target_email)
        return perm
    except HttpError as he:
        logger.error("Failed to emergency share folder %s: %s", folder_id, he)
        return None


# -------------------------------------------------------------------
# Main connection test
# -------------------------------------------------------------------
from django.contrib.auth.decorators import user_passes_test
from django.views.decorators.http import require_POST

def is_deployment_manager(user):
    return user.is_superuser or user.is_staff



@require_POST
@user_passes_test(is_deployment_manager)
def test_google_drive_connection(request, *, user=None):
    """
    Test connectivity to Google Drive using current SiteSettings.
    Supports Service Account and OAuth authentication.
    Automatically creates root folder if missing.
    Handles offset-naive vs offset-aware expiry datetimes for OAuth.
    """
    from apps.EasyDocs.models import SiteSettings
    from datetime import datetime, timezone
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    site_settings = SiteSettings.objects.first()
    if not site_settings:
        return JsonResponse({"success": False, "message": "No SiteSettings configured"}, status=400)

    logger.info("Starting Google Drive connection test for company=%s", site_settings.company_name)

    creds = None
    account_type = None

    try:
        # ---------------------------
        # STEP 1: Authenticate
        # ---------------------------
        if site_settings.google_drive_service_account_key_encrypted:
            logger.info("Using service account authentication...")
            from apps.EasyDocs.files.security import decrypt_value
            service_account_info = decrypt_value(site_settings.google_drive_service_account_key_encrypted)
            creds = service_account.Credentials.from_service_account_info(
                service_account_info,
                scopes=["https://www.googleapis.com/auth/drive"]
            )
            account_type = "service_account"

        elif user or request.user.is_authenticated:
            logger.info("Using OAuth user token authentication...")
            active_user = user or request.user
            from apps.EasyDocs.models import DriveOAuthToken
            token = DriveOAuthToken.objects.filter(user=active_user).first()
            if not token or not token.refresh_token_encrypted:
                return create_error_response(
                    site_settings=site_settings,
                    message="No valid OAuth token found for this user",
                    step="authenticate",
                )

            from apps.EasyDocs.files.security import credential_service
            creds_dict = {
                "token": credential_service.decrypt(token.access_token_encrypted),
                "refresh_token": credential_service.decrypt(token.refresh_token_encrypted),
                "client_id": site_settings.google_oauth_client_id,
                "client_secret": credential_service.decrypt(site_settings.google_oauth_client_secret_encrypted),
                "token_uri": "https://oauth2.googleapis.com/token",
            }

            creds = Credentials(
                token=creds_dict["token"],
                refresh_token=creds_dict["refresh_token"],
                client_id=creds_dict["client_id"],
                client_secret=creds_dict["client_secret"],
                token_uri=creds_dict["token_uri"],
            )

            # ---------------------------
            # Handle offset-naive vs offset-aware expiry
            # ---------------------------
            if creds.expiry and creds.expiry.tzinfo is None:
                logger.debug("Converting naive expiry datetime to UTC-aware")
                creds.expiry = creds.expiry.replace(tzinfo=timezone.utc)

            if creds.expired and creds.refresh_token:
                logger.info("Refreshing expired OAuth token...")
                creds.refresh(Request())

            account_type = "oauth_user"

        else:
            return create_error_response(
                site_settings=site_settings,
                message="No authentication method available",
                step="authenticate",
            )

        # ---------------------------
        # STEP 2: Build Drive service
        # ---------------------------
        logger.info("Building Google Drive service client...")
        drive_service = build("drive", "v3", credentials=creds)

        # ---------------------------
        # STEP 3: Validate or create root folder
        # ---------------------------
        root_folder_id = site_settings.google_drive_root_folder_id
        folder_info = None

        if root_folder_id:
            try:
                folder_info = drive_service.files().get(
                    fileId=root_folder_id,
                    fields="id, name, mimeType, webViewLink"
                ).execute()
                logger.info("Root folder exists: %s (%s)", folder_info.get("name"), folder_info.get("id"))
            except HttpError as e:
                if e.resp.status == 404:
                    logger.warning("Configured root folder not found in Drive. Will create a new one.")
                    root_folder_id = None
                else:
                    raise

        if not root_folder_id:
            folder_metadata = {"name": "EasyDocs_Root", "mimeType": "application/vnd.google-apps.folder"}
            folder = drive_service.files().create(body=folder_metadata, fields="id, name, webViewLink").execute()
            root_folder_id = folder.get("id")
            folder_info = folder
            site_settings.google_drive_root_folder_id = root_folder_id
            site_settings.save(update_fields=["google_drive_root_folder_id"])
            logger.info("Created new root folder 'EasyDocs_Root' with ID=%s", root_folder_id)

        # ---------------------------
        # STEP 4: Success response
        # ---------------------------
        return create_success_response(
            site_settings=site_settings,
            message="Google Drive connection successful",
            payload={
                "account_type": account_type,
                "root_folder_id": root_folder_id,
                "root_folder_name": folder_info.get("name") if folder_info else None,
                "root_folder_url": folder_info.get("webViewLink") if folder_info else None,
                "company_email": site_settings.company_email,
            }
        )

    except Exception as e:
        logger.exception("Error during Google Drive connection test")
        return create_error_response(
            site_settings=site_settings,
            message="Unexpected error during Google Drive connection test",
            error=e,
            step="unexpected",
        )


# -------------------------------------------------------------------
# Generate Deployment Key
# -------------------------------------------------------------------
@user_passes_test(is_deployment_manager)
def generate_deployment_key(request):
    """Generate a new deployment encryption key (for environment setup)."""
    try:
        new_key = credential_service.generate_deployment_key()
        return JsonResponse({
            "success": True,
            "deployment_key": new_key,
            "instructions": f"Add this to your environment: DEPLOYMENT_ENCRYPTION_KEY={new_key}",
            "docker_compose_example": f"environment:\n  - DEPLOYMENT_ENCRYPTION_KEY={new_key}",
        })
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)})



