# apps/EasyDocs/files/security.py
import os
import base64
import json
import logging
from typing import Optional, Dict, Any

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from django.conf import settings

logger = logging.getLogger(__name__)


class SecureCredentialService:
    def __init__(self):
        self.deployment_key = self._get_deployment_encryption_key()

    def _get_deployment_encryption_key(self) -> bytes:
        deployment_key = os.getenv("DEPLOYMENT_ENCRYPTION_KEY")
        if deployment_key:
            return deployment_key.encode()

        salt = (settings.SECRET_KEY + "GDRIVE_DEPLOYMENT").encode()[:16]
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        raw = kdf.derive(settings.SECRET_KEY.encode())
        fernet_key = base64.urlsafe_b64encode(raw)
        return fernet_key

    def generate_deployment_key(self) -> str:
        key = Fernet.generate_key()
        return key.decode()

    def encrypt(self, plaintext: str) -> str:
        try:
            f = Fernet(self.deployment_key)
            token = f.encrypt(plaintext.encode("utf-8"))
            return token.decode("utf-8")
        except Exception as e:
            logger.exception("Encryption failed: %s", e)
            raise

    def decrypt(self, token: str) -> str:
        try:
            f = Fernet(self.deployment_key)
            val = f.decrypt(token.encode("utf-8"))
            return val.decode("utf-8")
        except Exception as e:
            logger.exception("Decryption failed: %s", e)
            raise

    def encrypt_service_account_key(self, service_account_json: str) -> str:
        try:
            json.loads(service_account_json)
            return self.encrypt(service_account_json)
        except Exception as e:
            logger.error(f"Failed to encrypt service account key: {e}")
            raise ValueError("Invalid service account key format") from e

    def decrypt_service_account_key(self, encrypted_key: str) -> str:
        try:
            return self.decrypt(encrypted_key)
        except Exception as e:
            logger.error(f"Failed to decrypt service account key: {e}")
            raise ValueError("Failed to decrypt service account key") from e

    def validate_service_account_key(self, service_account_json: str) -> Dict[str, Any]:
        try:
            data = json.loads(service_account_json)
            required_fields = [
                "type",
                "project_id",
                "private_key_id",
                "private_key",
                "client_email",
            ]
            if not all(field in data for field in required_fields):
                raise ValueError("Missing required fields in service account key")
            if data.get("type") != "service_account":
                raise ValueError("Invalid service account type")
            return {
                "valid": True,
                "client_email": data["client_email"],
                "project_id": data["project_id"],
                "private_key_id": data["private_key_id"],
            }
        except json.JSONDecodeError:
            raise ValueError("Invalid JSON format")
        except Exception as e:
            raise ValueError(f"Service account validation failed: {e}")


credential_service = SecureCredentialService()


def build_credentials_for_user(user):
    """
    Build Credentials for a user using refresh token stored in DriveOAuthToken.
    The import of load_drive_credentials is done lazily to avoid circular imports.
    """
    # local import to prevent circular import at module import time
    from apps.EasyDocs.files.utils import load_drive_credentials
    from apps.EasyDocs.models import DriveOAuthToken

    creds_json = load_drive_credentials()
    if not creds_json:
        raise RuntimeError("OAuth client JSON missing")

    token_obj = getattr(user, "drive_oauth_token", None)
    if not token_obj or not token_obj.refresh_token_encrypted:
        return None

    try:
        refresh_token = credential_service.decrypt(token_obj.refresh_token_encrypted)
    except Exception as e:
        logger.exception("Failed to decrypt user's refresh token: %s", e)
        return None

    client_id = creds_json.get("client_id")
    client_secret = creds_json.get("client_secret")

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=(token_obj.scopes.split() if token_obj.scopes else ["https://www.googleapis.com/auth/drive.file"]),
    )

    try:
        creds.refresh(Request())
    except Exception as e:
        logger.exception("Failed to refresh credentials for user %s: %s", getattr(user, "pk", "<unknown>"), e)
        return None

    try:
        if creds.token:
            token_obj.access_token_encrypted = credential_service.encrypt(creds.token)
            token_obj.expiry = creds.expiry
            token_obj.save(update_fields=["access_token_encrypted", "expiry"])
    except Exception:
        logger.exception("Failed to persist refreshed access token")

    return creds
