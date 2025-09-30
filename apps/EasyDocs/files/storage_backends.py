import io
import logging
import mimetypes
import os
from django.core.files.storage import Storage
from django.conf import settings
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from googleapiclient.errors import HttpError
from django.core.files.storage import Storage, default_storage
from django.core.files.uploadedfile import UploadedFile
from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)

class GoogleDriveStorage:
    """
    Google Drive storage adapter.
    Automatically creates folders for clients or office documents.
    Logs every step for traceability.
    """

    def __init__(self, service_account_key_json=None, root_folder_id=None):
        self.service_account_key_json = service_account_key_json
        self.root_folder_id = root_folder_id
        self._service = None
        self._folder_cache = {}  # per-instance cache

        # Ensure root folder exists at init
        if not self.root_folder_id:
            logger.info("⚙️ No root folder configured. Creating default root folder 'EasyDocs_Root'.")
            try:
                self.root_folder_id = self._ensure_folder("EasyDocs_Root", parent_id=None)
                logger.info("📂 Root folder 'EasyDocs_Root' created with ID: %s", self.root_folder_id)

                # Save to DB settings if available
                try:
                    from apps.EasyDocs.models import SiteSettings
                    site_settings = SiteSettings.objects.first()
                    if site_settings:
                        site_settings.google_drive_root_folder_id = self.root_folder_id
                        site_settings.save(update_fields=["google_drive_root_folder_id"])
                        logger.info("✅ SiteSettings updated with new root folder ID")
                except Exception as db_e:
                    logger.warning("⚠️ Could not update SiteSettings with root folder ID: %s", db_e)

            except Exception as e:
                logger.error("❌ Failed to create root folder: %s", e, exc_info=True)
                raise

    @property
    def service(self):
        if self._service is None:
            self._service = self._build_service()
        return self._service

    def _build_service(self):
        if not self.service_account_key_json:
            raise ValueError("Service account JSON required")
        creds = service_account.Credentials.from_service_account_info(
            self.service_account_key_json,
            scopes=['https://www.googleapis.com/auth/drive']
        )
        service = build('drive', 'v3', credentials=creds, cache_discovery=False)
        logger.info("✅ Google Drive service built successfully")
        return service

    def _ensure_folder(self, folder_name: str, parent_id: str) -> str:
        """
        Ensure a folder exists under the given parent in Google Drive.
        Uses a per-request cache to avoid repeated API lookups.
        """
        cache_key = (folder_name, parent_id)
        if cache_key in self._folder_cache:
            logger.debug("📂 Cache hit: folder '%s' under parent %s → %s",
                         folder_name, parent_id, self._folder_cache[cache_key])
            return self._folder_cache[cache_key]

        logger.info("📂 Checking existence of folder '%s' under parent %s",
                    folder_name, parent_id or "root")

        try:
            # Query Google Drive for an existing folder
            if parent_id:
                query = (
                    f"name = '{folder_name}' and "
                    f"mimeType = 'application/vnd.google-apps.folder' and "
                    f"'{parent_id}' in parents and trashed = false"
                )
            else:
                query = (
                    f"name = '{folder_name}' and "
                    f"mimeType = 'application/vnd.google-apps.folder' and "
                    f"'root' in parents and trashed = false"
                )

            results = (
                self.service.files()
                .list(q=query, spaces="drive", fields="files(id, name)", pageSize=1)
                .execute()
            )
            items = results.get("files", [])

            if items:
                folder_id = items[0]["id"]
                logger.info("📂 Found existing folder '%s' (ID: %s)", folder_name, folder_id)
            else:
                # Create new folder
                file_metadata = {
                    "name": folder_name,
                    "mimeType": "application/vnd.google-apps.folder",
                }
                if parent_id:
                    file_metadata["parents"] = [parent_id]

                folder = (
                    self.service.files()
                    .create(body=file_metadata, fields="id, name, parents")
                    .execute()
                )
                folder_id = folder["id"]
                logger.info("📂 Created new folder '%s' (ID: %s)", folder_name, folder_id)

            # Cache the result
            self._folder_cache[cache_key] = folder_id
            return folder_id

        except Exception as e:
            logger.error("❌ Failed ensuring folder '%s' under parent %s: %s",
                         folder_name, parent_id, e, exc_info=True)
            raise

    def _save(self, relative_path: str, content):
        """
        Save file into Google Drive, ensuring folder structure is created.
        Example:
        clients/client_1/mutation/file.pdf
        office/contracts/file.pdf
        """
        try:
            if hasattr(content, "seek"):
                content.seek(0)

            parts = relative_path.strip("/").split("/")
            filename = parts[-1]
            folders = parts[:-1]

            logger.info("📂 Preparing to save file: %s", filename)
            logger.info("📂 Path parts: %s", parts)

            parent_id = self.root_folder_id
            current_path = []

            for folder_name in folders:
                current_path.append(folder_name)
                parent_id = self._ensure_folder(folder_name, parent_id)
                logger.info("📂 Ensured path '%s' → %s", "/".join(current_path), parent_id)

            # Detect MIME type
            mimetype, _ = mimetypes.guess_type(filename)
            mimetype = mimetype or "application/octet-stream"

            if hasattr(content, "seek"):
                content.seek(0)
                media_stream = content
            else:
                media_stream = io.BytesIO(content.read())

            media = MediaIoBaseUpload(media_stream, mimetype=mimetype, resumable=True)
            file_metadata = {"name": filename, "parents": [parent_id]}

            file = (
                self.service.files()
                .create(body=file_metadata, media_body=media, fields="id, name, parents")
                .execute()
            )

            logger.info(
                "✅ File uploaded: '%s' (ID: %s) in folder ID: %s",
                filename, file["id"], parent_id,
            )
            return file["id"]

        except Exception as e:
            logger.error("❌ Failed uploading file '%s': %s", relative_path, e, exc_info=True)
            raise

    def _open(self, file_id):
        """Download file from Drive"""
        try:
            request = self.service.files().get_media(fileId=file_id)
            file_stream = io.BytesIO()
            downloader = MediaIoBaseDownload(file_stream, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            file_stream.seek(0)
            logger.info("⬇️ Downloaded file from Drive (ID: %s)", file_id)
            return file_stream
        except Exception as e:
            logger.error("❌ Failed to download file from Drive ID %s: %s", file_id, e, exc_info=True)
            raise

    def delete(self, file_id):
        """Delete file from Drive"""
        try:
            self.service.files().delete(fileId=file_id).execute()
            logger.info("🗑️ Deleted file from Drive ID: %s", file_id)
            return True
        except Exception as e:
            logger.error("❌ Failed to delete file from Drive ID %s: %s", file_id, e, exc_info=True)
            return False

    def exists(self, file_id):
        """Check if a file exists in Drive"""
        try:
            self.service.files().get(fileId=file_id).execute()
            return True
        except HttpError as e:
            if e.resp.status == 404:
                return False
            logger.warning("⚠️ Drive exists check failed for %s: %s", file_id, e)
            return False

    def url(self, file_id):
        """Return file URL for viewing"""
        return f"https://drive.google.com/file/d/{file_id}/view"




from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
import logging

logger = logging.getLogger(__name__)


class UnifiedStorage:
    """
    Unified storage that decides where to save and provides helpers for
    open/url/delete/exists. It works with DriveAdapter returned by get_drive_storage().
    """

    def __init__(self):
        # don't hit DB at import time; lazy-get inside methods if needed
        self._drive_adapter = None

    def _get_drive_adapter(self):
        """
        Lazy-load the Google Drive adapter.
        Logs detailed reasons if the adapter is unavailable.
        """
        if self._drive_adapter is not None:
            return self._drive_adapter

        try:
            from apps.EasyDocs.files.utils import get_drive_storage

            # Attempt to get the adapter
            self._drive_adapter = get_drive_storage()

            if self._drive_adapter is None:
                logger.warning(
                    "Drive adapter returned None: check if service account or OAuth credentials are configured properly."
                )
            else:
                logger.info("✅ Drive adapter initialized successfully.")

        except ImportError as e:
            logger.error("⚠️ Drive adapter import failed: %s", e)
            self._drive_adapter = None
        except ValueError as e:
            logger.error("⚠️ Drive adapter initialization failed (likely missing credentials): %s", e)
            self._drive_adapter = None
        except Exception as e:
            logger.error("⚠️ Unexpected error initializing Drive adapter: %s", e, exc_info=True)
            self._drive_adapter = None

        return self._drive_adapter


    # existence helpers
    def _local_exists(self, relative_name: str) -> bool:
        try:
            return default_storage.exists(relative_name)
        except Exception as e:
            logger.warning("Local exists check failed for %s: %s", relative_name, e)
            return False

    def _drive_exists(self, file_id: str) -> bool:
        adapter = self._get_drive_adapter()
        if not adapter:
            return False
        try:
            return adapter.exists(file_id)
        except Exception as e:
            logger.warning("Drive exists check failed for %s: %s", file_id, e)
            return False

    # delete
    def delete(self, name_or_id, backend="local"):
        try:
            adapter = self._get_drive_adapter()
            if backend == "local":
                if self._local_exists(name_or_id):
                    default_storage.delete(name_or_id)
                    logger.info("Deleted %s from local", name_or_id)
                    return True
                return False

            if backend == "drive":
                if not adapter:
                    logger.warning("Delete requested for drive but adapter not available")
                    return False
                if adapter.exists(name_or_id):
                    return adapter.delete(name_or_id)
                return False

            if backend == "hybrid":
                ok = False
                if self._local_exists(name_or_id):
                    default_storage.delete(name_or_id)
                    ok = True
                if adapter and adapter.exists(name_or_id):
                    adapter.delete(name_or_id)
                    ok = True
                return ok
        except Exception as e:
            logger.error("Delete failed for %s (%s): %s", name_or_id, backend, e)
            return False

    # save_with_backend: returns (relative_path, backend, drive_file_id_or_none)
    def save_with_backend(self, relative_path: str, content):
        """
        Decide storage and save.
        Returns: (relative_path, backend, drive_file_id_or_none)

        Logs detailed info to trace why a file goes to Drive or Local.
        """
        logger.info("📂 Starting save workflow for: %s", relative_path)

        # Step 1: Try to initialize Drive
        adapter = self._get_drive_adapter()
        if not adapter:
            logger.warning(
                "🚫 Drive adapter unavailable. "
                "File '%s' will NOT be uploaded to Drive. "
                "Falling back to local storage.",
                relative_path,
            )
        else:
            logger.info("🔌 Drive adapter available, attempting Drive save...")

            try:
                # Step 2: Attempt Drive upload
                drive_id = adapter._save(relative_path, content)
                logger.info(
                    "✅ File saved to Google Drive: '%s' (Drive ID: %s)",
                    relative_path, drive_id
                )
                return relative_path, "drive", drive_id

            except Exception as e:
                # Step 3: Capture failure reason
                logger.warning(
                    "⚠️ Drive save failed for '%s': %s. "
                    "Falling back to local storage.",
                    relative_path, e,
                    exc_info=True
                )

        # Step 4: Always attempt local fallback
        try:
            from django.core.files.storage import default_storage

            logger.info("💾 Attempting local save for: %s", relative_path)
            default_storage.save(relative_path, content)

            logger.info(
                "✅ File saved locally: '%s'. "
                "Reason: Drive unavailable or failed.",
                relative_path
            )
            return relative_path, "local", None

        except Exception as e:
            # Step 5: Local also failed — log critically
            logger.error(
                "❌ Local save failed for '%s': %s. File was NOT saved anywhere!",
                relative_path, e,
                exc_info=True
            )
            return relative_path, "failed", None




    # open: for docs we prefer to use drive_file_id when backend == drive
    def open(self, document_instance):
        backend = document_instance.storage_backend
        adapter = self._get_drive_adapter()

        if backend == "drive":
            if not document_instance.drive_file_id:
                raise FileNotFoundError("No drive_file_id set on document")
            if not adapter:
                raise FileNotFoundError("Drive adapter not available")
            return adapter._open(document_instance.drive_file_id)

        if backend == "local":
            return default_storage.open(document_instance.doc_file.name, "rb")

        if backend == "hybrid":
            # prefer drive if possible
            try:
                if document_instance.drive_file_id and adapter:
                    return adapter._open(document_instance.drive_file_id)
            except Exception:
                pass
            return default_storage.open(document_instance.doc_file.name, "rb")

        raise FileNotFoundError("Unknown backend: %s" % backend)

    # url helper: for drive use drive_file_id
    def url(self, name_or_id, backend=None):
        adapter = self._get_drive_adapter()

        if backend is None:
            # guess: if looks like a drive id (no slash, short-ish), assume drive
            backend = "drive" if (not name_or_id or "/" not in name_or_id and len(name_or_id) < 128) else "local"

        if backend == "drive":
            if not adapter:
                return None
            return adapter.url(name_or_id)

        if backend in ("local", "hybrid"):
            try:
                return default_storage.url(name_or_id)
            except Exception as e:
                logger.warning("Local url resolution failed: %s", e)
                return None

        return None