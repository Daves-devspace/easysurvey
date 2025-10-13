import os
import logging
from django.core.management.base import BaseCommand
from django.core.files.base import ContentFile
from django.db import transaction
from apps.EasyDocs.models import Document, ClientDoc
from apps.EasyDocs.files.storage_backends import UnifiedStorage

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Re-save documents through unified storage with detailed reporting'

    def add_arguments(self, parser):
        parser.add_argument(
            '--document-type',
            choices=['office', 'client', 'all'],
            default='all',
            help='Which documents to process'
        )
        parser.add_argument(
            '--ids',
            nargs='+',
            type=int,
            help='Specific document IDs to process'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simulate without saving'
        )
        parser.add_argument(
            '--skip-errors',
            action='store_true',
            help='Continue processing even if some documents fail'
        )

    def handle(self, *args, **options):
        self.storage = UnifiedStorage()
        self.dry_run = options['dry_run']
        self.skip_errors = options['skip_errors']
        
        results = {
            'office': {'success': 0, 'error': 0, 'skipped': 0},
            'client': {'success': 0, 'error': 0, 'skipped': 0}
        }

        # Process documents based on type
        doc_type = options['document_type']
        document_ids = options['ids']

        if doc_type in ['office', 'all']:
            results['office'] = self.process_document_type(
                Document, 'Office Document', document_ids
            )

        if doc_type in ['client', 'all']:
            results['client'] = self.process_document_type(
                ClientDoc, 'Client Document', document_ids
            )

        # Final summary
        self.print_final_summary(results)

    def process_document_type(self, model, type_name, document_ids):
        """Process a specific type of documents"""
        self.stdout.write(f"\n🎯 Processing {type_name}s...")
        
        queryset = model.objects.all()
        if document_ids:
            queryset = queryset.filter(id__in=document_ids)
        
        total = queryset.count()
        results = {'success': 0, 'error': 0, 'skipped': 0}

        for i, doc in enumerate(queryset, 1):
            self.stdout.write(f"  [{i}/{total}] {doc.doc_name}...", ending=' ')
            
            try:
                success = self.process_single_document(doc, type_name)
                if success:
                    results['success'] += 1
                    self.stdout.write(self.style.SUCCESS("✅"))
                else:
                    results['skipped'] += 1
                    self.stdout.write(self.style.WARNING("⚠️"))
                    
            except Exception as e:
                results['error'] += 1
                self.stdout.write(self.style.ERROR("❌"))
                if not self.skip_errors:
                    raise
                logger.error(f"Error processing {type_name} {doc.id}: {e}")

        return results

    def process_single_document(self, doc, type_name):
        """Process a single document"""
        if self.dry_run:
            return True

        # Check if file exists and is accessible
        if not doc.doc_file or not doc.doc_file.name:
            self.stdout.write(f"(no file)", ending=' ')
            return False

        try:
            # Read file content
            with doc.doc_file.open('rb') as f:
                content = f.read()

            # Generate new path
            new_path = self.generate_document_path(doc, type_name)
            
            # Save through unified storage
            relative_path, backend, drive_file_id = self.storage.save_with_backend(
                new_path, ContentFile(content, name=os.path.basename(doc.doc_file.name))
            )

            # Update document
            with transaction.atomic():
                doc.storage_backend = backend
                doc.drive_file_id = drive_file_id
                doc.local_path = relative_path
                doc.status = 'uploaded'
                
                if relative_path != doc.doc_file.name:
                    doc.doc_file.name = relative_path
                
                doc.save()

            self.stdout.write(f"({backend})", ending=' ')
            return True

        except Exception as e:
            self.stdout.write(f"(error: {str(e)})", ending=' ')
            return False

    def generate_document_path(self, doc, type_name):
        """Generate appropriate path for document"""
        if hasattr(doc, 'get_full_drive_path'):
            return doc.get_full_drive_path()
        
        # Fallback path generation
        timestamp = doc.uploaded_at.strftime('%Y%m%d_%H%M%S')
        safe_name = f"{timestamp}_{doc.doc_name}"
        
        if type_name == 'Client Document':
            return f"clients/{doc.client.id}/{doc.doc_type.name}/{safe_name}"
        else:
            return f"office/{doc.doc_type.name}/{safe_name}"

    def print_final_summary(self, results):
        """Print final summary of processing"""
        self.stdout.write("\n" + "="*60)
        self.stdout.write("📊 FINAL MIGRATION SUMMARY")
        self.stdout.write("="*60)
        
        for doc_type, stats in results.items():
            total = sum(stats.values())
            if total > 0:
                self.stdout.write(
                    f"{doc_type.title()} Documents: "
                    f"✅ {stats['success']} successful, "
                    f"⚠️ {stats['skipped']} skipped, "
                    f"❌ {stats['error']} failed"
                )
        
        if self.dry_run:
            self.stdout.write(
                self.style.WARNING("\n💡 This was a DRY RUN - no changes were made")
            )