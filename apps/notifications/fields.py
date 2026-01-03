from django.db import models
from django.conf import settings
from cryptography.fernet import Fernet, MultiFernet

class EncryptedTextField(models.TextField):
    """
    A custom TextField that encrypts its content using the Fernet algorithm
    before saving to the DB, and decrypts it when reading.
    Compatible with Django 5.0+.
    """
    description = "Encrypted Text Field"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._fernet = None

    @property
    def fernet(self):
        """
        Initializes the MultiFernet instance using keys from settings.
        Lazily loaded so we don't access settings at import time.
        """
        if self._fernet:
            return self._fernet
        
        # Support both FERNET_KEYS (list) and FERNET_KEY (string)
        keys = getattr(settings, "FERNET_KEYS", None)
        if not keys:
            single_key = getattr(settings, "FERNET_KEY", None)
            if single_key:
                keys = [single_key]
        
        if not keys:
            raise ValueError(
                "EncryptedTextField requires 'FERNET_KEYS' or 'FERNET_KEY' in settings.py. "
                "Ensure you have generated a key using Fernet.generate_key()."
            )

        # Ensure keys are bytes
        validated_keys = [k.encode() if isinstance(k, str) else k for k in keys]
        
        try:
            self._fernet = MultiFernet([Fernet(k) for k in validated_keys])
        except Exception as e:
            raise ValueError(f"Invalid FERNET_KEY config: {e}")
            
        return self._fernet

    def get_prep_value(self, value):
        """Encrypt data before saving to DB."""
        value = super().get_prep_value(value)
        if value is None or value == "":
            return value
        
        # Encrypt the string
        encrypted_bytes = self.fernet.encrypt(value.encode('utf-8'))
        return encrypted_bytes.decode('utf-8')

    def from_db_value(self, value, expression, connection):
        """Decrypt data when loading from DB."""
        if value is None or value == "":
            return value
        
        try:
            decrypted_bytes = self.fernet.decrypt(value.encode('utf-8'))
            return decrypted_bytes.decode('utf-8')
        except Exception:
            # If decryption fails, it might be plain text or invalid data.
            # In a robust system, we raise the error so we know something is wrong.
            raise ValueError("Could not decrypt field value. Check your FERNET_KEYS.")