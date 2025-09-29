import logging
from django.core.files.storage import default_storage
from django.core.mail import EmailMessage
from django.shortcuts import get_object_or_404
from apps.EasyDocs.files.utils import get_drive_storage, log_audit
import mimetypes

logger = logging.getLogger(__name__)

def upload_document_with_strategy(document_instance, uploaded_file):
    try:
        from apps.EasyDocs.files.storage_backends import UnifiedStorage
        storage = UnifiedStorage()

        # prepare relative path (same pattern you already use)
        from django.utils import timezone
        now = document_instance.uploaded_at or timezone.now()

        if document_instance.__class__.__name__ == "Document":
            base_path = f"office/{(document_instance.doc_type.name or 'general').lower().replace(' ', '_')}/{now.year}/{now.month:02d}"
        else:
            base_path = f"clients/client_{document_instance.client.id}/{document_instance.doc_type.name.lower().replace(' ', '_')}/{now.year}/{now.month:02d}"

        filename = f"{now.strftime('%Y%m%d_%H%M%S')}_{uploaded_file.name}"
        relative_path = f"{base_path}/{filename}"

        # Save
        logger.info("Uploading document: %s", uploaded_file.name)
        saved_path, backend, drive_file_id = storage.save_with_backend(relative_path, uploaded_file)


        # Apply results cleanly:
        if backend == "local":
            # write to FileField (this sets the proper field internals)
            document_instance.doc_file.save(saved_path, uploaded_file, save=False)
            document_instance.drive_file_id = None
            document_instance.drive_url = None

        elif backend == "drive":
            # keep doc_file.name as relative_path (optional: set it for consistency)
            # do not store drive file id into doc_file.name, store in drive_file_id
            document_instance.doc_file.name = saved_path
            document_instance.drive_file_id = drive_file_id
            document_instance.drive_url = storage.url(drive_file_id, backend="drive")

        elif backend == "hybrid":
            document_instance.doc_file.save(saved_path, uploaded_file, save=False)
            document_instance.drive_file_id = drive_file_id
            document_instance.drive_url = storage.url(drive_file_id, backend="drive")

        else:
            logger.error("Document upload failed: unknown backend %s", backend)
            document_instance.status = "failed"
            document_instance.failure_reason = "Storage backend failure"
            document_instance.save()
            return False

        document_instance.storage_backend = backend
        document_instance.status = "uploaded"
        document_instance.failure_reason = None
        document_instance.save()

        logger.info("Document saved: backend=%s saved_path=%s drive_id=%s", backend, saved_path, drive_file_id)
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
        return storage.delete(doc.doc_file.name, backend=doc.storage_backend)
    except Exception as e:
        logger.error(f"❌ Storage delete failed for {doc.id}: {e}")
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