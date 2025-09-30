import logging
from django.utils import timezone
from django.core.files.storage import default_storage
from django.core.mail import EmailMessage
from django.shortcuts import get_object_or_404
from apps.EasyDocs.files.utils import get_drive_storage, log_audit
import mimetypes

logger = logging.getLogger(__name__)

def upload_document_with_strategy(document_instance, uploaded_file):
    """
    Uploads a document using UnifiedStorage.
    - Places client docs in: clients/client_<id>/<doc_type>/<timestamp>_<filename>
    - Office docs in: office/<doc_type>/<timestamp>_<filename>
    - No year/month subfolders (date baked into filename instead).
    """

    try:
        from apps.EasyDocs.files.storage_backends import UnifiedStorage
        storage = UnifiedStorage()

        # Use uploaded_at if already set, otherwise use now
        now = document_instance.uploaded_at or timezone.now()
        timestamp = now.strftime("%Y%m%d_%H%M%S")

        # Decide base path: office vs client
        if document_instance.__class__.__name__ == "Document":
            base_path = f"office/{(document_instance.doc_type.name or 'general').lower().replace(' ', '_')}"
        else:
            base_path = f"clients/client_{document_instance.client.id}/{document_instance.doc_type.name.lower().replace(' ', '_')}"

        # Build clean filename (timestamp included, no nested folders)
        filename = f"{timestamp}_{uploaded_file.name}"
        relative_path = f"{base_path}/{filename}"

        logger.info("Uploading document for %s: path=%s", 
                    getattr(document_instance, 'client', 'office'), 
                    relative_path)

        # Perform the actual save
        saved_path, backend, drive_file_id = storage.save_with_backend(relative_path, uploaded_file)

        # Handle results depending on backend
        if backend == "local":
            logger.debug("Saving file locally at %s", saved_path)
            document_instance.doc_file.save(saved_path, uploaded_file, save=False)
            document_instance.drive_file_id = None
            document_instance.drive_url = None

        elif backend == "drive":
            logger.debug("Saving file on Google Drive, id=%s", drive_file_id)
            document_instance.doc_file.name = saved_path
            document_instance.drive_file_id = drive_file_id
            document_instance.drive_url = storage.url(drive_file_id, backend="drive")

        elif backend == "hybrid":
            logger.debug("Hybrid save: local+drive, id=%s", drive_file_id)
            document_instance.doc_file.save(saved_path, uploaded_file, save=False)
            document_instance.drive_file_id = drive_file_id
            document_instance.drive_url = storage.url(drive_file_id, backend="drive")

        else:
            logger.error("Unknown backend %s while uploading %s", backend, relative_path)
            document_instance.status = "failed"
            document_instance.failure_reason = "Storage backend failure"
            document_instance.save()
            return False

        # Update final metadata
        document_instance.storage_backend = backend
        document_instance.status = "uploaded"
        document_instance.failure_reason = None
        document_instance.save()

        logger.info("Document upload complete: backend=%s path=%s drive_id=%s", 
                    backend, saved_path, drive_file_id)
        return True

    except Exception as e:
        logger.exception("Document upload failed: %s", e)
        document_instance.status = "failed"
        document_instance.failure_reason = str(e)
        document_instance.save()
        return False



def download_document_content(document_instance):
    """
    Get document content from appropriate storage
    """
    try:
        if document_instance.status == 'uploaded' and document_instance.drive_file_id:
            storage = get_drive_storage()
            if storage and storage.exists(document_instance.drive_file_id):
                return storage._open(document_instance.drive_file_id).read()
        
        elif document_instance.status == 'local' and document_instance.doc_file:
            if default_storage.exists(document_instance.doc_file.name):
                return document_instance.doc_file.read()
        
        return None
        
    except Exception as e:
        logger.error(f"Failed to get document content: {e}")
        return None



def delete_document_from_storage(doc):
    """
    Delete a document from its configured storage backend.
    """
    from apps.EasyDocs.files.storage_backends import UnifiedStorage
    storage = UnifiedStorage()
    
    try:
        backend = doc.storage_backend
        
        if backend == "drive":
            # For Drive, use the drive_file_id
            if not doc.drive_file_id:
                logger.warning(f"Document {doc.id} has backend='drive' but no drive_file_id")
                return False
            
            identifier = doc.drive_file_id
            logger.info(f"Deleting from Drive: {identifier}")
            
        elif backend in ("local", "hybrid"):
            # For local/hybrid, use the file path
            identifier = doc.doc_file.name
            logger.info(f"Deleting from {backend}: {identifier}")
            
        else:
            logger.warning(f"Unknown storage backend: {backend}")
            return False
        
        success = storage.delete(identifier, backend=backend)
        
        if success:
            logger.info(f"✅ Successfully deleted document {doc.id} from {backend}")
        else:
            logger.warning(f"⚠️ Delete returned False for document {doc.id} from {backend}")
            
        return success
        
    except Exception as e:
        logger.error(f"❌ Storage delete failed for document {doc.id}: {e}", exc_info=True)
        return False

def migrate_document_to_drive(document_instance):
    """
    Migrate local document to Drive
    """
    try:
        if document_instance.status != 'local' or not document_instance.doc_file:
            return False, "Document not available for migration"
        
        content = download_document_content(document_instance)
        if not content:
            return False, "Could not read document content"
        
        from apps.EasyDocs.files.storage_backends import UnifiedStorage
        storage = UnifiedStorage()
        
        # Save to Drive
        drive_path = storage._save(
            document_instance.doc_file.name,
            content,
            document_instance
        )
        
        if drive_path.startswith('drive:'):
            # Successfully migrated to Drive
            document_instance.save()
            return True, "Successfully migrated to Drive"
        else:
            return False, "Migration failed"
            
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        return False, str(e)
    
    
    
# Add these functions to your existing documents.py file

def check_document_exists(client, doc_name):
    """Check if a document with the same name exists for the client."""
    from apps.EasyDocs.models import ClientDoc
    return ClientDoc.objects.filter(client=client, doc_name=doc_name).exists()

def upload_to_drive(doc, folder_path=None):
    """
    Upload document to Google Drive - called by Celery tasks
    """
    from apps.EasyDocs.files.utils import get_drive_storage
    from django.core.files.storage import default_storage
    
    storage = get_drive_storage()
    if not storage:
        raise ValueError("Google Drive not configured")
    
    try:
        # Generate Drive path
        if folder_path:
            drive_path = f"{folder_path}/{doc.doc_name}"
        else:
            drive_path = doc.get_full_drive_path()
        
        # Get file content
        if not doc.doc_file:
            raise ValueError("Document file not available for upload")
        
        # Read file content
        file_content = doc.doc_file.read()
        
        # Upload to Drive
        drive_file_id = storage._save(drive_path, file_content)
        
        # Update document status
        doc.drive_file_id = drive_file_id
        doc.drive_url = storage.url(drive_file_id)
        doc.storage_backend = 'drive'
        doc.status = 'uploaded'
        doc.failure_reason = None
        
        # Remove local file since it's now in Drive (optional - based on strategy)
        # if default_storage.exists(doc.doc_file.name):
        #     default_storage.delete(doc.doc_file.name)
        
        doc.save()
        
        logger.info(f"Document {doc.id} uploaded to Drive: {drive_file_id}")
        return drive_file_id
        
    except Exception as e:
        logger.error(f"Failed to upload document {doc.id} to Drive: {e}")
        # Update document status to indicate failure
        doc.status = 'failed'
        doc.failure_reason = str(e)
        doc.save()
        raise

def send_doc_email_to_client_helper(request, client_id, doc_id):
    """
    Legacy function to maintain URL compatibility
    """
    return send_document_email(request, client_id, doc_id)

def get_document_download_url(doc, request=None):
    """
    Generate download URL for a document
    """
    from django.urls import reverse
    
    if isinstance(doc, ClientDoc):
        return reverse('download_client_document', kwargs={'doc_id': doc.id})
    else:
        return reverse('download_office_document', kwargs={'doc_id': doc.id})

def get_document_preview_url(doc, request=None):
    """
    Generate preview URL for a document
    """
    from django.urls import reverse
    
    if isinstance(doc, ClientDoc):
        return reverse('preview_client_document', kwargs={'client_id': doc.client.id, 'doc_id': doc.id})
    else:
        return reverse('preview_office_document', kwargs={'doc_id': doc.id})

def document_health_check():
    """
    Check health of all documents (for admin monitoring)
    """
    from apps.EasyDocs.models import ClientDoc, Document
    from django.core.files.storage import default_storage
    from apps.EasyDocs.files.utils import get_drive_storage
    
    health_report = {
        'total_documents': 0,
        'drive_documents': 0,
        'local_documents': 0,
        'failed_documents': 0,
        'orphaned_files': 0,
        'issues': []
    }
    
    try:
        # Check all documents
        all_docs = list(ClientDoc.objects.all()) + list(Document.objects.all())
        health_report['total_documents'] = len(all_docs)
        
        drive_storage = get_drive_storage()
        
        for doc in all_docs:
            if doc.status == 'uploaded':
                health_report['drive_documents'] += 1
                # Verify Drive file exists
                if drive_storage and not drive_storage.exists(doc.drive_file_id):
                    health_report['issues'].append(f"Drive file missing for document {doc.id}")
                    health_report['orphaned_files'] += 1
                    
            elif doc.status == 'local':
                health_report['local_documents'] += 1
                # Verify local file exists
                if doc.doc_file and not default_storage.exists(doc.doc_file.name):
                    health_report['issues'].append(f"Local file missing for document {doc.id}")
                    health_report['orphaned_files'] += 1
                    
            elif doc.status == 'failed':
                health_report['failed_documents'] += 1
        
        return health_report
        
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        health_report['issues'].append(f"Health check error: {str(e)}")
        return health_report