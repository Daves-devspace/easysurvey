# apps/EasyDocs/management/commands/fix_broken_uploads.py
import os
import logging
from pathlib import Path
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone

from apps.EasyDocs.models import Document, ClientDoc

logger = logging.getLogger("fix_broken_uploads")

# Configure logging
if not logger.handlers:
    logger.setLevel(logging.INFO)
    console_h = logging.StreamHandler()
    file_h = logging.FileHandler(os.path.join(settings.BASE_DIR, "fix_broken_uploads.log"))
    fmt = logging.Formatter("[%(levelname)s] %(asctime)s %(name)s %(message)s")
    console_h.setFormatter(fmt)
    file_h.setFormatter(fmt)
    logger.addHandler(console_h)
    logger.addHandler(file_h)


class Command(BaseCommand):
    help = "Fix documents with empty doc_file field by finding matching files on disk"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be fixed without actually updating database'
        )
        parser.add_argument(
            '--delete-unfixable',
            action='store_true',
            help='Delete records that cannot be fixed (no file found)'
        )

    def handle(self, *args, **options):
        self.dry_run = options.get('dry_run', False)
        self.delete_unfixable = options.get('delete_unfixable', False)
        self.fixed = 0
        self.not_found = 0
        self.deleted = 0

        if self.dry_run:
            logger.info("🔍 DRY RUN MODE - No changes will be made")

        media_root = Path(settings.MEDIA_ROOT)

        # Fix Documents
        self._fix_documents(media_root)
        
        # Fix ClientDocs
        self._fix_clientdocs(media_root)

        logger.info(f"\nFix completed:")
        logger.info(f"  ✅ Fixed: {self.fixed}")
        logger.info(f"  ❌ Not found: {self.not_found}")
        logger.info(f"  🗑️  Deleted: {self.deleted}")

    def _build_file_inventory(self, folder: Path):
        """Build inventory of files with metadata"""
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
                if filename not in inventory:
                    inventory[filename] = []
                
                inventory[filename].append({
                    'path': file_path,
                    'mtime_dt': mtime_dt,
                })
        
        return inventory

    def _find_matching_file(self, doc, file_inventory):
        """
        Find a file that matches this document based on:
        1. Timestamp (within 5 minutes of uploaded_at)
        2. Filename contains doc_name or vice versa
        """
        candidates = []
        
        for filename, file_list in file_inventory.items():
            for file_info in file_list:
                # Check timestamp match (within 5 minutes)
                time_diff = abs((doc.uploaded_at - file_info['mtime_dt']).total_seconds())
                
                if time_diff <= 300:  # 5 minutes
                    # Calculate similarity score
                    score = 0
                    
                    # Exact timestamp match
                    if time_diff < 60:
                        score += 100
                    
                    # Filename contains doc_name (without extension)
                    doc_name_base = doc.doc_name.lower().replace(' ', '_')
                    filename_base = filename.lower().replace(' ', '_')
                    
                    if doc_name_base in filename_base or filename_base in doc_name_base:
                        score += 50
                    
                    # Doc type in filename
                    if hasattr(doc, 'doc_type') and doc.doc_type:
                        doc_type_slug = doc.doc_type.name.lower().replace(' ', '_')
                        if doc_type_slug in filename_base:
                            score += 25
                    
                    candidates.append({
                        'file_info': file_info,
                        'filename': filename,
                        'time_diff': time_diff,
                        'score': score,
                    })
        
        if not candidates:
            return None
        
        # Sort by score (highest first), then time_diff (lowest first)
        candidates.sort(key=lambda x: (-x['score'], x['time_diff']))
        return candidates[0]

    def _fix_documents(self, media_root: Path):
        logger.info("\n" + "="*60)
        logger.info("Fixing Document records...")
        logger.info("="*60)
        
        # Find broken documents
        broken = Document.objects.filter(doc_file='') | Document.objects.filter(doc_file__isnull=True)
        total = broken.count()
        
        logger.info(f"Found {total} Documents with empty doc_file")
        
        # Build file inventory - check both old and new locations
        office_files = {}
        for search_path in [media_root / "office", media_root / "documents" / "office"]:
            if search_path.exists():
                logger.info(f"Searching in: {search_path.relative_to(media_root)}")
                found = self._build_file_inventory(search_path)
                office_files.update(found)
                logger.info(f"  Found {len(found)} files")
        
        if not office_files:
            logger.warning("No office files found to match against!")
            return
        
        for doc in broken:
            try:
                match = self._find_matching_file(doc, office_files)
                
                if match:
                    file_path = match['file_info']['path']
                    new_relative_path = os.path.relpath(file_path, media_root)
                    time_diff_mins = match['time_diff'] / 60
                    
                    if self.dry_run:
                        logger.info(
                            f"[DRY-RUN] Would fix Document #{doc.id} ('{doc.doc_name}'):\n"
                            f"  '' -> {new_relative_path}\n"
                            f"  (time diff: {time_diff_mins:.1f} mins, score: {match['score']})"
                        )
                    else:
                        doc.doc_file = new_relative_path
                        doc.status = 'uploaded'
                        doc.save(update_fields=['doc_file', 'status'])
                        logger.info(
                            f"✅ Fixed Document #{doc.id} ('{doc.doc_name}'):\n"
                            f"   '' -> {new_relative_path}"
                        )
                    self.fixed += 1
                    
                    # Remove from inventory
                    filename = match['filename']
                    office_files[filename] = [
                        f for f in office_files[filename] 
                        if f['path'] != file_path
                    ]
                    if not office_files[filename]:
                        del office_files[filename]
                else:
                    self.not_found += 1
                    logger.warning(
                        f"❌ Document #{doc.id} ('{doc.doc_name}'): No matching file found\n"
                        f"   Uploaded: {doc.uploaded_at}"
                    )
                    
                    if self.delete_unfixable and not self.dry_run:
                        doc.delete()
                        self.deleted += 1
                        logger.info(f"🗑️  Deleted unfixable Document #{doc.id}")
                    
            except Exception as e:
                logger.error(f"❌ Failed to fix Document #{doc.id}: {e}", exc_info=True)

    def _fix_clientdocs(self, media_root: Path):
        logger.info("\n" + "="*60)
        logger.info("Fixing ClientDoc records...")
        logger.info("="*60)
        
        # Find broken clientdocs
        broken = ClientDoc.objects.filter(doc_file='') | ClientDoc.objects.filter(doc_file__isnull=True)
        total = broken.count()
        
        logger.info(f"Found {total} ClientDocs with empty doc_file")
        
        # Build file inventory - check both old and new locations
        client_files = {}
        for search_path in [media_root / "client_docs", media_root / "documents" / "clients"]:
            if search_path.exists():
                logger.info(f"Searching in: {search_path.relative_to(media_root)}")
                found = self._build_file_inventory(search_path)
                client_files.update(found)
                logger.info(f"  Found {len(found)} files")
        
        if not client_files:
            logger.warning("No client files found to match against!")
            return
        
        for doc in broken:
            try:
                match = self._find_matching_file(doc, client_files)
                
                if match:
                    file_path = match['file_info']['path']
                    new_relative_path = os.path.relpath(file_path, media_root)
                    time_diff_mins = match['time_diff'] / 60
                    
                    if self.dry_run:
                        logger.info(
                            f"[DRY-RUN] Would fix ClientDoc #{doc.id} ('{doc.doc_name}', Client: {doc.client.first_name}):\n"
                            f"  '' -> {new_relative_path}\n"
                            f"  (time diff: {time_diff_mins:.1f} mins, score: {match['score']})"
                        )
                    else:
                        doc.doc_file = new_relative_path
                        doc.status = 'uploaded'
                        doc.save(update_fields=['doc_file', 'status'])
                        logger.info(
                            f"✅ Fixed ClientDoc #{doc.id} ('{doc.doc_name}', Client: {doc.client.first_name}):\n"
                            f"   '' -> {new_relative_path}"
                        )
                    self.fixed += 1
                    
                    # Remove from inventory
                    filename = match['filename']
                    client_files[filename] = [
                        f for f in client_files[filename] 
                        if f['path'] != file_path
                    ]
                    if not client_files[filename]:
                        del client_files[filename]
                else:
                    self.not_found += 1
                    logger.warning(
                        f"❌ ClientDoc #{doc.id} ('{doc.doc_name}', Client: {doc.client.first_name}): No matching file found\n"
                        f"   Uploaded: {doc.uploaded_at}"
                    )
                    
                    if self.delete_unfixable and not self.dry_run:
                        doc.delete()
                        self.deleted += 1
                        logger.info(f"🗑️  Deleted unfixable ClientDoc #{doc.id}")
                    
            except Exception as e:
                logger.error(f"❌ Failed to fix ClientDoc #{doc.id}: {e}", exc_info=True)