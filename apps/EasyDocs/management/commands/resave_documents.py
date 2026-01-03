# apps/EasyDocs/management/commands/resave_documents.py
import os
import logging
from pathlib import Path

from django.core.management.base import BaseCommand
from django.conf import settings

from apps.EasyDocs.models import Document, ClientDoc

logger = logging.getLogger("document_resave")

# Configure logging
if not logger.handlers:
    logger.setLevel(logging.INFO)
    console_h = logging.StreamHandler()
    file_h = logging.FileHandler(os.path.join(settings.BASE_DIR, "document_resave.log"))
    fmt = logging.Formatter("[%(levelname)s] %(asctime)s %(name)s %(message)s")
    console_h.setFormatter(fmt)
    file_h.setFormatter(fmt)
    logger.addHandler(console_h)
    logger.addHandler(file_h)


class Command(BaseCommand):
    help = "Update doc_file URLs for existing Document/ClientDoc records to match actual file locations (no file movement)"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without actually saving changes'
        )
        parser.add_argument(
            '--fix-broken',
            action='store_true',
            help='Only process documents where the current path does not exist'
        )

    def handle(self, *args, **options):
        self.dry_run = options.get('dry_run', False)
        self.fix_broken = options.get('fix_broken', False)
        self.updated = 0
        self.not_found = 0
        self.already_correct = 0
        self.failed = 0

        if self.dry_run:
            logger.info("🔍 DRY RUN MODE - No changes will be made")

        media_root = Path(settings.MEDIA_ROOT)

        logger.info("Starting document URL update from database records...")
        
        # Process Documents
        self._process_documents(media_root)
        
        # Process ClientDocs
        self._process_clientdocs(media_root)

        logger.info(f"Resave completed. Updated: {self.updated}, Already Correct: {self.already_correct}, Not Found: {self.not_found}, Failed: {self.failed}")

    def _find_actual_file(self, media_root: Path, filename: str, search_dirs: list) -> Path:
        """Search for a file in multiple possible locations."""
        for search_dir in search_dirs:
            search_path = media_root / search_dir
            if not search_path.exists():
                continue
            
            # Search recursively for the file
            for file_path in search_path.rglob(filename):
                if file_path.is_file():
                    return file_path
        
        return None

    def _process_documents(self, media_root: Path):
        logger.info("Processing Document records...")
        documents = Document.objects.all()
        
        for doc in documents:
            try:
                current_path_str = str(doc.doc_file)
                current_full_path = media_root / current_path_str
                
                # If file exists at current path, skip or log
                if current_full_path.exists():
                    if self.fix_broken:
                        # Only fixing broken ones, skip this
                        continue
                    self.already_correct += 1
                    logger.debug(f"Document #{doc.id} path is correct: {current_path_str}")
                    continue
                
                # File doesn't exist at recorded path, try to find it
                filename = os.path.basename(current_path_str)
                logger.info(f"Document #{doc.id}: File not found at {current_path_str}, searching for {filename}...")
                
                # Search in common locations
                search_dirs = [
                    "documents/office",
                    "documents",
                    "office_documents",
                    "uploads",
                ]
                
                actual_file = self._find_actual_file(media_root, filename, search_dirs)
                
                if actual_file:
                    new_relative_path = os.path.relpath(actual_file, media_root)
                    
                    if self.dry_run:
                        logger.info(f"[DRY-RUN] Would update Document #{doc.id}: {current_path_str} -> {new_relative_path}")
                        self.updated += 1
                    else:
                        old_path = doc.doc_file
                        doc.doc_file = new_relative_path
                        doc.save(update_fields=['doc_file'])
                        self.updated += 1
                        logger.info(f"✅ Updated Document #{doc.id}: {old_path} -> {new_relative_path}")
                else:
                    self.not_found += 1
                    logger.warning(f"❌ Document #{doc.id}: Could not find file {filename} anywhere in media directory")
                    
            except Exception as e:
                self.failed += 1
                logger.error(f"❌ Failed to process Document #{doc.id}: {e}", exc_info=True)

    def _process_clientdocs(self, media_root: Path):
        logger.info("Processing ClientDoc records...")
        clientdocs = ClientDoc.objects.all()
        
        for doc in clientdocs:
            try:
                current_path_str = str(doc.doc_file)
                current_full_path = media_root / current_path_str
                
                # If file exists at current path, skip or log
                if current_full_path.exists():
                    if self.fix_broken:
                        continue
                    self.already_correct += 1
                    logger.debug(f"ClientDoc #{doc.id} path is correct: {current_path_str}")
                    continue
                
                # File doesn't exist at recorded path, try to find it
                filename = os.path.basename(current_path_str)
                logger.info(f"ClientDoc #{doc.id}: File not found at {current_path_str}, searching for {filename}...")
                
                # Search in common locations, prioritizing client-specific folders
                search_dirs = [
                    f"documents/clients/{doc.client.id}",
                    "documents/clients",
                    "client_docs",
                    "documents",
                    "uploads",
                ]
                
                actual_file = self._find_actual_file(media_root, filename, search_dirs)
                
                if actual_file:
                    new_relative_path = os.path.relpath(actual_file, media_root)
                    
                    if self.dry_run:
                        logger.info(f"[DRY-RUN] Would update ClientDoc #{doc.id}: {current_path_str} -> {new_relative_path}")
                        self.updated += 1
                    else:
                        old_path = doc.doc_file
                        doc.doc_file = new_relative_path
                        doc.save(update_fields=['doc_file'])
                        self.updated += 1
                        logger.info(f"✅ Updated ClientDoc #{doc.id}: {old_path} -> {new_relative_path}")
                else:
                    self.not_found += 1
                    logger.warning(f"❌ ClientDoc #{doc.id}: Could not find file {filename} anywhere in media directory")
                    
            except Exception as e:
                self.failed += 1
                logger.error(f"❌ Failed to process ClientDoc #{doc.id}: {e}", exc_info=True)