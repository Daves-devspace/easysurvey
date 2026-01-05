# apps/EasyDocs/management/commands/resave_documents.py
import os
import logging
from pathlib import Path
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone

from apps.EasyDocs.models import Document, ClientDoc, Client

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
    help = "Match orphaned database records to actual files in office_documents/ and client_docs/"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without actually saving changes'
        )
        parser.add_argument(
            '--show-orphans',
            action='store_true',
            help='Show database records that could not be matched to files'
        )

    def handle(self, *args, **options):
        self.dry_run = options.get('dry_run', False)
        self.show_orphans = options.get('show_orphans', False)
        self.updated = 0
        self.already_correct = 0
        self.not_matched = 0
        self.failed = 0

        if self.dry_run:
            logger.info("🔍 DRY RUN MODE - No changes will be made")

        media_root = Path(settings.MEDIA_ROOT)

        logger.info("Starting document resave from orphaned database records...")
        
        # Build file inventory from disk
        office_files = self._build_file_inventory(media_root / "office_documents")
        client_files = self._build_file_inventory(media_root / "client_docs")
        
        logger.info(f"Found {len(office_files)} files in office_documents/")
        logger.info(f"Found {len(client_files)} files in client_docs/")
        
        # Process Documents
        self._process_documents(media_root, office_files)
        
        # Process ClientDocs
        self._process_clientdocs(media_root, client_files)

        logger.info(f"Resave completed. Updated: {self.updated}, Already Correct: {self.already_correct}, Not Matched: {self.not_matched}, Failed: {self.failed}")

    def _build_file_inventory(self, folder: Path):
        """
        Build inventory of all files in a folder with their metadata.
        Returns: dict[filename] = {path, size, mtime, mtime_dt}
        """
        inventory = {}
        if not folder.exists():
            return inventory
        
        for file_path in folder.rglob("*"):
            if file_path.is_file():
                stat = file_path.stat()
                mtime_dt = timezone.datetime.fromtimestamp(
                    stat.st_mtime,
                    tz=timezone.get_current_timezone()
                )
                
                filename = file_path.name
                
                # Handle duplicate filenames by storing as list
                if filename not in inventory:
                    inventory[filename] = []
                
                inventory[filename].append({
                    'path': file_path,
                    'size': stat.st_size,
                    'mtime': stat.st_mtime,
                    'mtime_dt': mtime_dt,
                })
        
        return inventory

    def _find_best_match(self, doc, file_inventory):
        """
        Try to match a database record to an actual file.
        Uses uploaded_at timestamp, client ID in filename (for ClientDoc), and file size.
        """
        # Get all possible files (we don't know the original filename)
        candidates = []
        
        # Check if this is a ClientDoc (has client attribute)
        is_clientdoc = hasattr(doc, 'client')
        client_id_str = str(doc.client.id) if is_clientdoc else None
        
        for filename, file_list in file_inventory.items():
            for file_info in file_list:
                # Calculate time difference between upload and file mtime
                time_diff = abs((doc.uploaded_at - file_info['mtime_dt']).total_seconds())
                
                # Allow up to 2 hours difference (7200 seconds) for flexibility
                if time_diff <= 7200:
                    # Bonus score if client ID appears in filename
                    has_client_id = False
                    if is_clientdoc and client_id_str and client_id_str in filename:
                        has_client_id = True
                    
                    candidates.append({
                        'file_info': file_info,
                        'time_diff': time_diff,
                        'filename': filename,
                        'has_client_id': has_client_id,
                    })
        
        if not candidates:
            return None
        
        # Sort by: 1) client ID match (if applicable), 2) time difference
        # This ensures files with matching client IDs are preferred
        candidates.sort(key=lambda x: (not x['has_client_id'], x['time_diff']))
        
        # Return the best match
        return candidates[0]

    def _process_documents(self, media_root: Path, file_inventory):
        logger.info("Processing Document records...")
        
        # Only process documents with broken paths
        documents = Document.objects.filter(
            doc_file__in=['1', '2', '3', '4', '5', '6', '7', '8', '9', '0']  # Common broken values
        ) | Document.objects.filter(doc_file__isnull=True) | Document.objects.filter(doc_file='')
        
        total = documents.count()
        logger.info(f"Found {total} Documents with broken paths")
        
        for doc in documents:
            try:
                current_path_str = str(doc.doc_file or "")
                
                # Try to find a matching file
                match = self._find_best_match(doc, file_inventory)
                
                if match:
                    file_path = match['file_info']['path']
                    new_relative_path = os.path.relpath(file_path, media_root)
                    time_diff_mins = match['time_diff'] / 60
                    
                    if self.dry_run:
                        logger.info(
                            f"[DRY-RUN] Document #{doc.id} ('{doc.doc_name}'): "
                            f"{current_path_str} -> {new_relative_path} "
                            f"(time diff: {time_diff_mins:.1f} mins)"
                        )
                        self.updated += 1
                    else:
                        old_path = doc.doc_file
                        doc.doc_file = new_relative_path
                        doc.save(update_fields=['doc_file'])
                        self.updated += 1
                        logger.info(
                            f"✅ Document #{doc.id} ('{doc.doc_name}'): "
                            f"{old_path} -> {new_relative_path}"
                        )
                    
                    # Remove matched file from inventory to avoid duplicate matches
                    filename = match['filename']
                    file_inventory[filename] = [
                        f for f in file_inventory[filename] 
                        if f['path'] != file_path
                    ]
                    if not file_inventory[filename]:
                        del file_inventory[filename]
                else:
                    self.not_matched += 1
                    if self.show_orphans:
                        logger.warning(
                            f"❌ Document #{doc.id} ('{doc.doc_name}'): "
                            f"Could not find matching file (uploaded: {doc.uploaded_at})"
                        )
                    
            except Exception as e:
                self.failed += 1
                logger.error(f"❌ Failed to process Document #{doc.id}: {e}", exc_info=True)

    def _process_clientdocs(self, media_root: Path, file_inventory):
        logger.info("Processing ClientDoc records...")
        
        # Only process clientdocs with broken paths
        clientdocs = ClientDoc.objects.filter(
            doc_file__in=['1', '2', '3', '4', '5', '6', '7', '8', '9', '0']
        ) | ClientDoc.objects.filter(doc_file__isnull=True) | ClientDoc.objects.filter(doc_file='')
        
        total = clientdocs.count()
        logger.info(f"Found {total} ClientDocs with broken paths")
        
        for doc in clientdocs:
            try:
                current_path_str = str(doc.doc_file or "")
                
                # Try to find a matching file
                match = self._find_best_match(doc, file_inventory)
                
                if match:
                    file_path = match['file_info']['path']
                    new_relative_path = os.path.relpath(file_path, media_root)
                    time_diff_mins = match['time_diff'] / 60
                    
                    if self.dry_run:
                        logger.info(
                            f"[DRY-RUN] ClientDoc #{doc.id} ('{doc.doc_name}', Client: {doc.client.first_name}): "
                            f"{current_path_str} -> {new_relative_path} "
                            f"(time diff: {time_diff_mins:.1f} mins)"
                        )
                        self.updated += 1
                    else:
                        old_path = doc.doc_file
                        doc.doc_file = new_relative_path
                        doc.save(update_fields=['doc_file'])
                        self.updated += 1
                        logger.info(
                            f"✅ ClientDoc #{doc.id} ('{doc.doc_name}', Client: {doc.client.first_name}): "
                            f"{old_path} -> {new_relative_path}"
                        )
                    
                    # Remove matched file from inventory
                    filename = match['filename']
                    file_inventory[filename] = [
                        f for f in file_inventory[filename] 
                        if f['path'] != file_path
                    ]
                    if not file_inventory[filename]:
                        del file_inventory[filename]
                else:
                    self.not_matched += 1
                    if self.show_orphans:
                        logger.warning(
                            f"❌ ClientDoc #{doc.id} ('{doc.doc_name}', Client: {doc.client.first_name}): "
                            f"Could not find matching file (uploaded: {doc.uploaded_at})"
                        )
                    
            except Exception as e:
                self.failed += 1
                logger.error(f"❌ Failed to process ClientDoc #{doc.id}: {e}", exc_info=True)