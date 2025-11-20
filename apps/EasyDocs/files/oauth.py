# apps/EasyDocs/files/oauth.py
import logging
import os
from urllib.parse import urlparse
from django.shortcuts import redirect
from django.urls import reverse
from django.views import View
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.utils.http import url_has_allowed_host_and_scheme
from google_auth_oauthlib.flow import Flow
from google.auth.exceptions import GoogleAuthError

# Allow OAuth over HTTP for local development
# WARNING: Remove this in production and use HTTPS
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

from google.auth.exceptions import RefreshError
from django.utils.decorators import method_decorator
from apps.EasyDocs.files.security import credential_service

from apps.EasyDocs.models import DriveOAuthToken, SiteSettings
from apps.EasyDocs.files.connection import is_deployment_manager
from apps.EasyDocs.files.utils import (
    get_oauth_client_config,
    pick_and_validate_redirect_uri,
    _build_service_from_oauth,
)
from googleapiclient.discovery import build as google_build
from googleapiclient.errors import HttpError
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests
from datetime import timezone

logger = logging.getLogger(__name__)


def _safe_next_url(request, fallback: str = None) -> str:
    candidate = request.GET.get("next") or request.META.get("HTTP_REFERER") or fallback or "/"
    parsed = urlparse(candidate)
    if not parsed.netloc:
        return candidate
    request_host = request.META.get("HTTP_HOST") or request.get_host()
    allowed_hosts = {request_host, request.get_host()}
    if url_has_allowed_host_and_scheme(candidate, allowed_hosts=allowed_hosts, require_https=False):
        return candidate
    logger.warning("Rejected unsafe next URL: %s; falling back to %s", candidate, fallback or "/")
    return fallback or "/"


@user_passes_test(is_deployment_manager)
def drive_oauth_start(request):
    """
    Start OAuth: validate credentials and redirect URI, store safe next in session, then redirect to Google.
    Uses OIDC scopes and login_hint when company email is set to steer account selection.
    """
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

    redirect_uri, diagnostics = pick_and_validate_redirect_uri(request, allowed_uris)

    logger.info("Generated redirect_uri=%s; allowed_uris=%s", redirect_uri, allowed_uris)
    if diagnostics:
        logger.error("OAuth redirect URI validation failed: %s", diagnostics)
        messages.error(request, "OAuth redirect URI problem — please add the exact redirect URI to Google Cloud Console.")
        messages.warning(request, f"Add this exact URI: {redirect_uri}")
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
        # Request OIDC + email + drive scopes so we can verify identity server-side
        scopes=[
            "https://www.googleapis.com/auth/drive",            # Full Drive access
            "openid",                                           # Identity verification
            "https://www.googleapis.com/auth/userinfo.email"   # Correct email access
        ]

        flow = Flow.from_client_config(client_config, scopes=scopes)
        flow.redirect_uri = redirect_uri

        # Use login_hint to bias account selection if company_email present
        site_settings = SiteSettings.objects.first()
        login_hint = (site_settings.company_email or "").strip() if site_settings and site_settings.company_email else None

        # prompt: request consent (refresh token) and force account selection for clarity
        prompt = "consent select_account"

        auth_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt=prompt,
            login_hint=login_hint,
        )

        request.session["oauth_next_url"] = next_url
        request.session["drive_oauth_state"] = state

        logger.info("Starting OAuth flow; redirect_uri=%s next=%s login_hint=%s", redirect_uri, next_url, login_hint)
        return redirect(auth_url)

    except GoogleAuthError as e:
        logger.exception("Failed initialising OAuth flow")
        messages.error(request, f"Failed to start OAuth flow: {e}")
        return redirect(next_url)


@user_passes_test(is_deployment_manager)
def drive_oauth_callback(request):
    """
    Finish OAuth flow:
    - validate redirect URI
    - fetch token
    - verify id_token (preferred) or userinfo (fallback)
    - ensure authenticated email matches SiteSettings.company_email (if set)
    - persist encrypted tokens only on successful verification
    """
    cfg = get_oauth_client_config()
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
        # Use same scopes as start (openid + email + drive)
        flow = Flow.from_client_config(client_config, scopes=[
            "https://www.googleapis.com/auth/drive",            # Full Drive access
            "openid",                                           # Identity verification
            "https://www.googleapis.com/auth/userinfo.email"   # Correct email access
        ], state=state)
        flow.redirect_uri = redirect_uri
        flow.fetch_token(authorization_response=request.build_absolute_uri())
        creds = flow.credentials

        if not creds or not creds.refresh_token:
            messages.error(request, "No refresh token received. Re-run authorization with 'offline' access.")
            logger.warning("OAuth callback returned no refresh_token")
            return redirect(next_url)

        # ---- Verify identity (prefer id_token verification) ----
        auth_email = None
        email_verified = False
        id_valid = False

        # 1) Try to verify the id_token if present
        idt = getattr(creds, "id_token", None)
        if idt:
            try:
                idinfo = google_id_token.verify_oauth2_token(idt, google_requests.Request(), client_id)
                auth_email = (idinfo.get("email") or "").strip().lower()
                email_verified = bool(idinfo.get("email_verified") or idinfo.get("verified_email"))
                # optional: check hd claim if SiteSettings restricts domain
                id_valid = True
                logger.debug("id_token verified for email=%s", auth_email)
            except Exception as e:
                logger.warning("id_token verification failed: %s — falling back to userinfo", e)
                id_valid = False

        # 2) Fallback: call oauth2.userinfo endpoint server-side
        if not id_valid:
            try:
                oauth2_service = google_build("oauth2", "v2", credentials=creds, cache_discovery=False)
                userinfo = oauth2_service.userinfo().get().execute()
                auth_email = (userinfo.get("email") or "").strip().lower()
                # userinfo uses 'verified_email' or 'email_verified' depending on API; check either
                email_verified = bool(userinfo.get("verified_email") or userinfo.get("email_verified"))
                logger.debug("userinfo fetched email=%s verified=%s", auth_email, email_verified)
            except HttpError as e:
                logger.exception("Failed to fetch userinfo from Google: %s", e)
                messages.error(request, "Failed to verify Google account email. Please try again.")
                return redirect(next_url)
            except Exception as e:
                logger.exception("Unexpected error fetching userinfo: %s", e)
                messages.error(request, "Failed to verify Google account email. Please try again.")
                return redirect(next_url)

        # 3) Enforce email_verified
        if not email_verified:
            logger.warning("Authenticated Google account email not verified: %s", auth_email)
            messages.error(request, "Google account email is not verified; cannot use this account.")
            return redirect(next_url)

        # 4) Compare against expected company email if configured
        site_settings = SiteSettings.objects.first()
        expected_email = (site_settings.company_email or "").strip().lower() if site_settings and site_settings.company_email else None

        if expected_email:
            if not auth_email:
                logger.warning("No email returned from Google; rejecting OAuth callback")
                messages.error(request, "Could not determine authenticated Google email. Authorization cancelled.")
                return redirect(next_url)

            if auth_email != expected_email:
                logger.warning("Rejected OAuth callback: authenticated email '%s' does not match expected '%s'", auth_email, expected_email)
                messages.error(
                    request,
                    f"Authorized account ({auth_email}) does not match required company account ({expected_email}). Please sign in with the company Google account."
                )
                return redirect(next_url)

        # ---- Persist tokens now that identity is verified ----
        DriveOAuthToken.objects.update_or_create(
            # store under the admin user who performed the auth for traceability
            user=request.user,
            defaults={
                "refresh_token_encrypted": credential_service.encrypt(creds.refresh_token),
                "access_token_encrypted": credential_service.encrypt(creds.token) if creds.token else None,
                "token_expiry": creds.expiry.replace(tzinfo=timezone.utc) if creds.expiry and creds.expiry.tzinfo is None else creds.expiry,
                "scopes": ",".join(creds.scopes) if creds.scopes else "",
                "needs_reauth": False,
            }
        )

        messages.success(request, "Google Drive OAuth authorization successful.")
        logger.info("Stored OAuth tokens for user=%s (confirmed email=%s)", request.user, auth_email)

    except GoogleAuthError as e:
        logger.exception("Google OAuth error: %s", e)
        messages.error(request, f"Google OAuth error: {e}")
    except Exception as e:
        logger.exception("Failed to persist OAuth tokens: %s", e)
        messages.error(request, f"Failed to store tokens: {e}")

    return redirect(next_url)


@method_decorator(user_passes_test(is_deployment_manager), name="dispatch")
class RefreshDriveTokenView(View):
    """
    CBV to manually refresh the single company OAuth token.
    Accessible only by deployment/admin users.
    """

    def get(self, request, *args, **kwargs):
        referer = request.META.get("HTTP_REFERER", "/")
        token_obj = DriveOAuthToken.objects.first()
        if not token_obj:
            messages.error(request, "No company OAuth token found. Please authorize first.")
            return redirect(referer)

        try:
            # _build_service_from_oauth will attempt refresh and update DB (or raise on invalid token)
            _, creds = _build_service_from_oauth()
            messages.success(request, f"Company OAuth token refreshed successfully. Expiry: {creds.expiry}")
            logger.info("Frontend refresh: Company OAuth token refreshed successfully")
        except RefreshError:
            messages.error(request, "Token is expired or revoked — re-authorization required.")
            logger.warning("Frontend refresh: Token expired/revoked")
        except ValueError as e:
            messages.error(request, f"Failed to refresh token: {e}")

            # mark token as needing reauthorization
            token_obj = DriveOAuthToken.objects.first()
            if token_obj:
                try:
                    token_obj.needs_reauth = True
                    token_obj.save(update_fields=["needs_reauth"])
                except Exception:
                    logger.exception("Failed to mark token_obj as needs_reauth after failed refresh")

            return redirect(referer)

