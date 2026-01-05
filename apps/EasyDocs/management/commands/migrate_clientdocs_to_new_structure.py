# apps/EasyDocs/management/commands/migrate_clientdocs_to_new_structure.py
import os
import shutil
import logging
from pathlib import Path

from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone

from apps.EasyDocs.models import ClientDoc

logger = logging.getLogger("clientdoc_migration")

# Configure logging
if not logger.handlers:
    logger.setLevel(logging.INFO)
    console_h = logging.StreamHandler()
    file_h = logging.FileHandler(os.path.join(settings.BASE_DIR, "clientdoc_migration.log"))
    fmt = logging.Formatter("[%(levelname)s] %(asctime)s %(name)s %(message)s")
    console_h.setFormatter(fmt)
    file_h.setFormatter(fmt)
    logger.addHandler(console_h)
    logger.addHandler(file_h)


class Command(BaseCommand):
    help = "Migrate ClientDocs from client_docs/ to new documents/clients/<client_id>/ structure"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without actually moving files or updating database'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=50,
            help='Process records in batches (default: 50)'
        )

    def handle(self, *args, **options):
        self.dry_run = options.get('dry_run', False)
        self.batch_size = options.get('batch_size', 50)
        self.migrated = 0
        self.already_new = 0
        self.failed = 0
        self.skipped = 0

        if self.dry_run:
            logger.info("🔍 DRY RUN MODE - No changes will be made")

        media_root = Path(settings.MEDIA_ROOT)

        # Get all ClientDocs with old paths
        old_clientdocs = ClientDoc.objects.filter(
            doc_file__startswith='client_docs/'
        ).select_related('client', 'doc_type')

        total = old_clientdocs.count()
        logger.info(f"Found {total} ClientDocs to migrate from client_docs/ to new structure")

        if total == 0:
            logger.info("✅ No ClientDocs need migration!")
            return

        # Process in batches to avoid memory issues
        processed = 0
        for clientdoc in old_clientdocs.iterator(chunk_size=self.batch_size):
            processed += 1
            try:
                self._migrate_clientdoc(clientdoc, media_root)
                
                # Progress update every 50 records
                if processed % 50 == 0:
                    logger.info(f"Progress: {processed}/{total} ({(processed/total)*100:.1f}%)")
                    
            except Exception as e:
                self.failed += 1
                logger.error(f"❌ Failed to migrate ClientDoc #{clientdoc.id}: {e}", exc_info=True)

        logger.info(f"\nMigration completed:")
        logger.info(f"  ✅ Migrated: {self.migrated}")
        logger.info(f"  ✅ Already in new location: {self.already_new}")
        logger.info(f"  ⚠️  Skipped (file not found): {self.skipped}")
        logger.info(f"  ❌ Failed: {self.failed}")

    def _migrate_clientdoc(self, clientdoc, media_root: Path):
        """
        Migrate a single ClientDoc from old to new structure.
        ONLY updates doc_file field - all other data preserved!
        """
        # Convert FieldFile to string
        current_path = str(clientdoc.doc_file.name) if clientdoc.doc_file else ""
        
        # Skip if empty or None
        if not current_path:
            self.skipped += 1
            logger.warning(f"⚠️  ClientDoc #{clientdoc.id}: Empty doc_file path")
            return
        
        # Skip if already in new location
        if current_path.startswith('documents/clients/'):
            self.already_new += 1
            logger.debug(f"ClientDoc #{clientdoc.id} already in new location")
            return

        # Build current full path
        current_full_path = media_root / current_path
        
        # Check if file exists
        if not current_full_path.exists():
            self.skipped += 1
            logger.warning(f"⚠️  ClientDoc #{clientdoc.id}: File not found at {current_path}")
            return

        # Generate new path based on new schema
        # documents/clients/<client_id>/<doc_type>/<year>/<month>/filename
        year = clientdoc.uploaded_at.year
        month = f"{clientdoc.uploaded_at.month:02d}"
        
        # Sanitize doc_type name to remove special characters
        safe_doc_type = self._sanitize_path_component(clientdoc.doc_type.name)
        
        new_folder = (
            media_root / "documents" / "clients" / 
            str(clientdoc.client.id) / safe_doc_type / 
            str(year) / month
        )
        
        # Original filename
        original_filename = current_full_path.name
        new_file_path = new_folder / original_filename
        
        # Handle duplicates by adding suffix
        new_file_path = self._unique_path(new_file_path)
        
        # Calculate new relative path
        new_relative_path = os.path.relpath(new_file_path, media_root)

        if self.dry_run:
            logger.info(
                f"[DRY-RUN] ClientDoc #{clientdoc.id} ('{clientdoc.doc_name}', Client: {clientdoc.client.first_name}):\n"
                f"  FROM: {current_path}\n"
                f"  TO:   {new_relative_path}"
            )
            self.migrated += 1
            return

        # Create destination folder with proper error handling
        try:
            new_folder.mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            logger.error(
                f"❌ Permission denied creating folder {new_folder}. "
                f"Run with sudo or fix permissions: sudo chown -R $USER:$USER /app/media/"
            )
            raise

        # Move the physical file
        try:
            shutil.move(str(current_full_path), str(new_file_path))
        except Exception as e:
            logger.error(f"❌ Failed to move file for ClientDoc #{clientdoc.id}: {e}")
            self.failed += 1
            return

        # Update ONLY the doc_file field in database
        old_path = clientdoc.doc_file
        clientdoc.doc_file = new_relative_path
        clientdoc.save(update_fields=['doc_file'])  # ✅ ONLY updates doc_file!

        self.migrated += 1
        logger.info(
            f"✅ ClientDoc #{clientdoc.id} ('{clientdoc.doc_name}', Client: {clientdoc.client.first_name}):\n"
            f"   {old_path} → {new_relative_path}"
        )

    def _sanitize_path_component(self, name: str) -> str:
        """
        Sanitize a string to be safe for use in file paths.
        Removes or replaces characters that can cause issues.
        """
        import re
        # Convert to lowercase
        name = name.lower()
        # Replace spaces and special chars with underscores
        name = re.sub(r'[^a-z0-9_-]', '_', name)
        # Remove multiple consecutive underscores
        name = re.sub(r'_+', '_', name)
        # Remove leading/trailing underscores
        name = name.strip('_')
        # Fallback if empty
        return name if name else 'unknown'

    def _unique_path(self, p: Path) -> Path:
        """If path exists, append numeric suffix before extension until unique."""
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
            
            # Safety check - prevent infinite loop
            if counter > 1000:
                raise Exception(f"Could not find unique filename after 1000 attempts for {p}")