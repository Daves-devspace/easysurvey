import logging
import os
from urllib.parse import urlparse
from django.shortcuts import redirect
from django.urls import reverse
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.utils.http import url_has_allowed_host_and_scheme
from google_auth_oauthlib.flow import Flow
from google.auth.exceptions import GoogleAuthError

# Allow OAuth over HTTP for local development
# WARNING: Remove this in production and use HTTPS
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

from apps.EasyDocs.files.security import credential_service
from apps.EasyDocs.models import DriveOAuthToken, SiteSettings
from apps.EasyDocs.files.connection import is_deployment_manager
from apps.EasyDocs.files.utils import get_oauth_client_config, pick_and_validate_redirect_uri, _build_redirect_uri

logger = logging.getLogger(__name__)


def _safe_next_url(request, fallback: str = None) -> str:
    """
    Determine a safe `next`/return URL:
    - prefer explicit next param
    - then referer header
    - finally fallback (defaults to root if not provided)
    Validates that the returned URL is the same host as the request (prevents open redirect).
    """
    candidate = request.GET.get("next") or request.META.get("HTTP_REFERER") or fallback or "/"

    # if candidate is a path (relative), that's safe
    parsed = urlparse(candidate)
    if not parsed.netloc:
        return candidate

    # If candidate is full URL, ensure host is allowed/same host
    # Get the actual host with port from request
    request_host = request.META.get('HTTP_HOST') or request.get_host()
    allowed_hosts = {request_host, request.get_host()}
    
    if url_has_allowed_host_and_scheme(candidate, allowed_hosts=allowed_hosts, require_https=False):
        return candidate

    # otherwise fallback to safe fallback
    logger.warning("Rejected unsafe next URL: %s; falling back to %s", candidate, fallback or "/")
    return fallback or "/"


@user_passes_test(is_deployment_manager)
def drive_oauth_start(request):
    """
    Start OAuth: validate credentials and redirect URI, store safe next in session, then redirect to Google.
    On failure, returns to the referring page.
    """
    # Get the page user came from (stays on same page on error)
    next_url = _safe_next_url(request)
    
    cfg = get_oauth_client_config()

    if not cfg:
        messages.error(request, "OAuth client ID/secret not configured in Site Settings.")
        return redirect(next_url)

    client_id = cfg.get("client_id")
    client_secret = cfg.get("client_secret")
    allowed_uris = cfg.get("allowed_redirect_uris", [])

    if not client_id or not client_secret:
        messages.error(request, "OAuth client ID or client secret missing in Site Settings.")
        return redirect(next_url)

    # derive redirect_uri and validate against allowed URIs; get diagnostics if mismatch
    redirect_uri, diagnostics = pick_and_validate_redirect_uri(request, allowed_uris)
    
    # Debug logging
    logger.info(f"🔍 Generated redirect_uri: {redirect_uri}")
    logger.info(f"🔍 Allowed URIs: {allowed_uris}")
    
    if diagnostics:
        logger.error("OAuth redirect URI validation failed: %s", diagnostics)
        messages.error(request, "OAuth redirect URI problem — please add the exact redirect URI to Google Cloud Console.")
        # Show the exact URI that needs to be added
        messages.warning(request, f"Add this exact URI: {redirect_uri}")
        # present a few helpful diagnostics to the UI
        for line in diagnostics[:6]:
            messages.info(request, line)
        return redirect(next_url)

    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }

    try:
        flow = Flow.from_client_config(client_config, scopes=["https://www.googleapis.com/auth/drive"])
        flow.redirect_uri = redirect_uri
        auth_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",  # Must be string "true", not boolean True
            prompt="consent",
        )
        # persist the next_url and state for callback
        request.session["oauth_next_url"] = next_url
        request.session["drive_oauth_state"] = state
        
        # Debug: log the full auth URL
        logger.error("="*80)
        logger.error(f"🔍 REDIRECT_URI BEING SENT TO GOOGLE: {redirect_uri}")
        logger.error(f"🔍 CLIENT_ID: {client_id[:20]}...")
        logger.error(f"🔍 FULL AUTH URL: {auth_url}")
        logger.error("="*80)
        
        logger.info("Starting OAuth flow; redirecting to Google consent screen; redirect_uri=%s next=%s", redirect_uri, next_url)
        return redirect(auth_url)
    except GoogleAuthError as e:
        logger.exception("Failed initialising OAuth flow")
        messages.error(request, f"Failed to start OAuth flow: {e}")
        return redirect(next_url)


@user_passes_test(is_deployment_manager)
def drive_oauth_callback(request):
    """
    Finish OAuth flow: validate redirect URI again, fetch token, persist encrypted tokens,
    and return the user to the saved next/referrer.
    On failure, returns to the page where OAuth was initiated.
    """
    cfg = get_oauth_client_config()
    # Get the saved return URL, fallback to root
    next_url = request.session.pop("oauth_next_url", "/")

    if not cfg:
        messages.error(request, "OAuth client credentials not configured in Site Settings.")
        return redirect(next_url)

    client_id = cfg.get("client_id")
    client_secret = cfg.get("client_secret")
    allowed_uris = cfg.get("allowed_redirect_uris", [])

    if not client_id or not client_secret:
        messages.error(request, "OAuth client ID or client secret missing.")
        return redirect(next_url)

    # re-derive and re-validate redirect_uri for safety
    redirect_uri, diagnostics = pick_and_validate_redirect_uri(request, allowed_uris)
    if diagnostics:
        logger.error("Redirect URI validation failed on callback: %s", diagnostics)
        messages.error(request, "Redirect URI validation failed on callback. Check Google Cloud Console entries.")
        messages.warning(request, f"Expected URI: {redirect_uri}")
        for line in diagnostics[:6]:
            messages.info(request, line)
        return redirect(next_url)

    state = request.session.get("drive_oauth_state")
    if not state:
        messages.error(request, "OAuth state missing in session - please restart the authorization flow.")
        return redirect(next_url)

    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }

    try:
        flow = Flow.from_client_config(client_config, scopes=["https://www.googleapis.com/auth/drive"], state=state)
        flow.redirect_uri = redirect_uri
        flow.fetch_token(authorization_response=request.build_absolute_uri())

        creds = flow.credentials
        if not creds or not creds.refresh_token:
            messages.error(request, "No refresh token received. Re-run authorization with 'offline' access.")
            logger.warning("OAuth callback returned no refresh_token")
            return redirect(next_url)

        from datetime import timezone

        DriveOAuthToken.objects.update_or_create(
            user=request.user,
            defaults={
                "refresh_token_encrypted": credential_service.encrypt_text(creds.refresh_token),
                "access_token_encrypted": credential_service.encrypt_text(creds.token) if creds.token else None,
                "token_expiry": creds.expiry.replace(tzinfo=timezone.utc) if creds.expiry and creds.expiry.tzinfo is None else creds.expiry,
                "scopes": ",".join(creds.scopes) if creds.scopes else "",
            }
        )

        messages.success(request, "Google Drive OAuth authorization successful.")
        logger.info("Stored OAuth tokens for user=%s", request.user)
    except GoogleAuthError as e:
        logger.exception("Google OAuth error: %s", e)
        messages.error(request, f"Google OAuth error: {e}")
    except Exception as e:
        logger.exception("Failed to persist OAuth tokens: %s", e)
        messages.error(request, f"Failed to store tokens: {e}")

    return redirect(next_url)