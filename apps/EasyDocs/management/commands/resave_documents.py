# apps/EasyDocs/management/commands/migrate_files_to_unified.py
import os
import shutil
import logging
from pathlib import Path

from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
from django.core.files import File

from django.contrib.auth import get_user_model

# Models from your app
from apps.EasyDocs.models import Document, ClientDoc, DocType, Client

logger = logging.getLogger("document_migration")

# Configure logging: console + file
if not logger.handlers:
    logger.setLevel(logging.INFO)
    console_h = logging.StreamHandler()
    file_h = logging.FileHandler(os.path.join(settings.BASE_DIR, "document_migration.log"))
    fmt = logging.Formatter("[%(levelname)s] %(asctime)s %(name)s %(message)s")
    console_h.setFormatter(fmt)
    file_h.setFormatter(fmt)
    logger.addHandler(console_h)
    logger.addHandler(file_h)


class Command(BaseCommand):
    help = "Migrate files in media/office_documents and media/client_docs into new Document/ClientDoc unified layout"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without actually moving files or updating database'
        )
        parser.add_argument(
            '--update-existing',
            action='store_true',
            help='Update existing Document/ClientDoc records instead of creating new ones'
        )

    def handle(self, *args, **options):
        self.dry_run = options.get('dry_run', False)
        self.update_existing = options.get('update_existing', False)
        self.migrated = 0
        self.updated = 0
        self.failed = 0
        self.skipped = 0

        if self.dry_run:
            logger.info("🔍 DRY RUN MODE - No changes will be made")

        media_root = Path(settings.MEDIA_ROOT)
        old_office = media_root / "office_documents"
        old_client = media_root / "client_docs"

        # Defaults
        User = get_user_model()
        default_user = User.objects.filter(is_superuser=True).first() or User.objects.first()
        if not default_user:
            logger.error("No user found in DB to assign as uploaded_by. Create at least one user and retry.")
            return

        default_client = Client.objects.first()
        if not default_client:
            logger.warning("No Client found — client-docs will be assigned to the first client when available (or skipped).")

        logger.info("Starting migration from filesystem.")
        
        # Office files
        self._migrate_office_folder(old_office, media_root, default_user)
        
        # Client files
        self._migrate_client_folder(old_client, media_root, default_user, default_client)

        logger.info(f"Migration completed. Migrated: {self.migrated}, Updated: {self.updated}, Skipped: {self.skipped}, Failed: {self.failed}")

    def _iter_files_recursive(self, folder: Path):
        """Yield Path objects for files inside folder (recursive)."""
        if not folder.exists():
            return
        for p in folder.rglob("*"):
            if p.is_file():
                yield p

    def _safe_get_or_create_doctype(self, candidate_name: str):
        name = (candidate_name or "").strip()
        if not name:
            name = "Migrated"
        doc_type, created = DocType.objects.get_or_create(name=name)
        if created:
            logger.info(f"Created DocType '{name}'.")
        return doc_type

    def _find_existing_document(self, file_path: Path, media_root: Path):
        """Try to find existing Document record that references this file."""
        # Calculate the current relative path from media root
        try:
            current_relative = os.path.relpath(file_path, media_root)
        except ValueError:
            return None
        
        # Also try the original filename
        filename = file_path.name
        
        # Look for Document with matching path or filename
        existing = Document.objects.filter(
            doc_file__icontains=filename
        ).first()
        
        if existing:
            logger.debug(f"Found existing Document #{existing.id} for {filename}")
        
        return existing

    def _find_existing_clientdoc(self, file_path: Path, media_root: Path, client_obj):
        """Try to find existing ClientDoc record that references this file."""
        try:
            current_relative = os.path.relpath(file_path, media_root)
        except ValueError:
            return None
        
        filename = file_path.name
        
        # Look for ClientDoc with matching path or filename for this client
        existing = ClientDoc.objects.filter(
            client=client_obj,
            doc_file__icontains=filename
        ).first()
        
        if existing:
            logger.debug(f"Found existing ClientDoc #{existing.id} for {filename}")
        
        return existing

    def _migrate_office_folder(self, old_office: Path, media_root: Path, default_user):
        if not old_office.exists():
            logger.warning(f"Office folder not found: {old_office}")
            return

        logger.info(f"Scanning office folder: {old_office}")
        for file_path in self._iter_files_recursive(old_office):
            try:
                # Check if file is already in new location
                if "documents/office" in str(file_path):
                    logger.debug(f"Skipping already migrated file: {file_path}")
                    self.skipped += 1
                    continue

                # Infer doc_type from immediate subfolder under office_documents (if any)
                rel = file_path.relative_to(old_office)
                parts = rel.parts
                doc_type_name = None
                if len(parts) > 1:
                    doc_type_name = parts[0]
                else:
                    fname = file_path.name
                    if "_" in fname:
                        doc_type_name = fname.split("_", 1)[0]

                doc_type = self._safe_get_or_create_doctype(doc_type_name)

                # Preserve timestamp from file mtime
                uploaded_at = timezone.datetime.fromtimestamp(
                    file_path.stat().st_mtime, 
                    tz=timezone.get_current_timezone()
                )

                # New folder: documents/office/<doc_type>/<YYYY>/<MM>
                year = uploaded_at.year
                month = f"{uploaded_at.month:02d}"
                safe_doc_type = doc_type.name.replace(" ", "_").lower()
                dest_folder = media_root / "documents" / "office" / safe_doc_type / str(year) / month
                
                if not self.dry_run:
                    dest_folder.mkdir(parents=True, exist_ok=True)

                # Keep original filename
                dest_file_name = file_path.name
                dest_path = dest_folder / dest_file_name

                # Check for existing document record
                existing_doc = None
                if self.update_existing:
                    existing_doc = self._find_existing_document(file_path, media_root)

                # Only add suffix if physical file exists AND we're creating new record
                if not existing_doc:
                    dest_path = self._unique_path(dest_path)

                new_relative_path = os.path.relpath(dest_path, settings.MEDIA_ROOT)

                if self.dry_run:
                    action = "UPDATE" if existing_doc else "CREATE"
                    logger.info(f"[DRY-RUN] {action}: {file_path} -> {new_relative_path}")
                    self.migrated += 1
                    continue

                # Move the physical file
                shutil.move(str(file_path), str(dest_path))

                if existing_doc:
                    # Update existing record
                    old_path = existing_doc.doc_file
                    existing_doc.doc_file = new_relative_path
                    existing_doc.doc_type = doc_type
                    existing_doc.storage_backend = "local"
                    existing_doc.status = "uploaded"
                    existing_doc.save()
                    self.updated += 1
                    logger.info(f"✏️  Updated Document #{existing_doc.id}: {old_path} -> {new_relative_path}")
                else:
                    # Create new Document instance
                    doc = Document(
                        doc_name=os.path.splitext(dest_file_name)[0],
                        doc_file=new_relative_path,
                        doc_type=doc_type,
                        uploaded_by=default_user,
                        location="Office",
                        reference="MIGRATED",
                        storage_backend="local",
                        status="uploaded",
                        uploaded_at=uploaded_at
                    )
                    doc.save()
                    self.migrated += 1
                    logger.info(f"✅ Created Document #{doc.id}: {new_relative_path} (DocType: {doc_type.name})")

            except Exception as e:
                self.failed += 1
                logger.error(f"❌ Failed to migrate office file {file_path}: {e}", exc_info=True)

    def _migrate_client_folder(self, old_client: Path, media_root: Path, default_user, default_client):
        if not old_client.exists():
            logger.warning(f"Client folder not found: {old_client}")
            return

        logger.info(f"Scanning client folder: {old_client}")
        for file_path in self._iter_files_recursive(old_client):
            try:
                # Check if file is already in new location
                if "documents/clients" in str(file_path):
                    logger.debug(f"Skipping already migrated file: {file_path}")
                    self.skipped += 1
                    continue

                # Determine client from parent folders
                rel = file_path.relative_to(old_client)
                parts = rel.parts
                client_obj = None
                candidate = None
                
                if len(parts) > 1:
                    candidate = parts[0]
                else:
                    fname = file_path.name
                    if fname.lower().startswith("client_") and "_" in fname:
                        candidate = fname.split("_", 1)[0]

                if candidate:
                    try:
                        if candidate.startswith("client_"):
                            cid = int(candidate.split("_", 1)[1])
                        else:
                            cid = int(candidate)
                        client_obj = Client.objects.filter(pk=cid).first()
                    except Exception:
                        client_obj = Client.objects.filter(first_name__iexact=candidate).first()

                if not client_obj:
                    client_obj = default_client

                if not client_obj:
                    logger.error(f"No client resolved for {file_path}; skipping.")
                    self.failed += 1
                    continue

                # Infer doc type
                doc_type_name = None
                if len(parts) > 2:
                    doc_type_name = parts[1]
                else:
                    fname = file_path.name
                    if "_" in fname:
                        doc_type_name = fname.split("_", 1)[0]

                doc_type = self._safe_get_or_create_doctype(doc_type_name)

                uploaded_at = timezone.datetime.fromtimestamp(
                    file_path.stat().st_mtime, 
                    tz=timezone.get_current_timezone()
                )

                # New folder: documents/clients/<client_id>/<doc_type>/<YYYY>/<MM>
                year = uploaded_at.year
                month = f"{uploaded_at.month:02d}"
                safe_doc_type = doc_type.name.replace(" ", "_").lower()
                dest_folder = media_root / "documents" / "clients" / str(client_obj.id) / safe_doc_type / str(year) / month
                
                if not self.dry_run:
                    dest_folder.mkdir(parents=True, exist_ok=True)

                dest_file_name = file_path.name
                dest_path = dest_folder / dest_file_name

                # Check for existing ClientDoc record
                existing_doc = None
                if self.update_existing:
                    existing_doc = self._find_existing_clientdoc(file_path, media_root, client_obj)

                if not existing_doc:
                    dest_path = self._unique_path(dest_path)

                new_relative_path = os.path.relpath(dest_path, settings.MEDIA_ROOT)

                if self.dry_run:
                    action = "UPDATE" if existing_doc else "CREATE"
                    logger.info(f"[DRY-RUN] {action}: {file_path} -> {new_relative_path}")
                    self.migrated += 1
                    continue

                # Move the physical file
                shutil.move(str(file_path), str(dest_path))

                if existing_doc:
                    # Update existing record
                    old_path = existing_doc.doc_file
                    existing_doc.doc_file = new_relative_path
                    existing_doc.doc_type = doc_type
                    existing_doc.storage_backend = "local"
                    existing_doc.status = "uploaded"
                    existing_doc.save()
                    self.updated += 1
                    logger.info(f"✏️  Updated ClientDoc #{existing_doc.id}: {old_path} -> {new_relative_path}")
                else:
                    # Create new ClientDoc instance
                    client_doc = ClientDoc(
                        client=client_obj,
                        doc_name=os.path.splitext(dest_file_name)[0],
                        doc_file=new_relative_path,
                        doc_type=doc_type,
                        uploaded_by=default_user,
                        storage_backend="local",
                        status="uploaded",
                        uploaded_at=uploaded_at
                    )
                    client_doc.save()
                    self.migrated += 1
                    logger.info(f"✅ Created ClientDoc #{client_doc.id}: {new_relative_path} (Client: {client_obj}, DocType: {doc_type.name})")

            except Exception as e:
                self.failed += 1
                logger.error(f"❌ Failed to migrate client file {file_path}: {e}", exc_info=True)

    def _unique_path(self, p: Path) -> Path:
        """If p exists, append a numeric suffix before extension until unique."""
        if not p.exists():
            return p
        base = p.stem
        suffix = p.suffix
        parent = p.parent
        counter = 1
        while True:
            candidate = parent / f"{base}_{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1