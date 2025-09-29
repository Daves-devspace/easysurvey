from celery import shared_task
import logging
from apps.EasyDocs.files.documents import migrate_document_to_drive

logger = logging.getLogger(__name__)

@shared_task(bind=True, max_retries=3)
def migrate_documents_to_drive_task(self):
    """
    Task to migrate all local documents to Drive
    """
    from apps.EasyDocs.models import ClientDoc, Document
    
    try:
        # Get documents that are local only
        local_client_docs = ClientDoc.objects.filter(status='local')
        local_office_docs = Document.objects.filter(status='local')
        
        success_count = 0
        total_count = local_client_docs.count() + local_office_docs.count()
        
        for doc in list(local_client_docs) + list(local_office_docs):
            success, message = migrate_document_to_drive(doc)
            if success:
                success_count += 1
                logger.info(f"Migrated document {doc.id} to Drive")
            else:
                logger.warning(f"Failed to migrate document {doc.id}: {message}")
        
        logger.info(f"Migration completed: {success_count}/{total_count} documents migrated")
        return {
            "success_count": success_count,
            "total_count": total_count,
            "message": f"Migrated {success_count} of {total_count} documents to Drive"
        }
        
    except Exception as exc:
        logger.error(f"Migration task failed: {exc}")
        raise self.retry(exc=exc, countdown=300)  # Retry after 5 minutes

@shared_task
def cleanup_orphaned_files_task():
    """
    Clean up orphaned files in storage
    """
    from apps.EasyDocs.models import ClientDoc, Document
    from django.core.files.storage import default_storage
    
    try:
        # Get all valid file references
        all_docs = list(ClientDoc.objects.all()) + list(Document.objects.all())
        valid_files = set()
        
        for doc in all_docs:
            if doc.doc_file:
                valid_files.add(doc.doc_file.name)
            if doc.drive_file_id:
                valid_files.add(f"drive:{doc.drive_file_id}")
        
        # This would need more sophisticated implementation
        logger.info(f"Cleanup task would check {len(valid_files)} valid files")
        
    except Exception as e:
        logger.error(f"Cleanup task failed: {e}")
        
        


@shared_task
def send_document_email_task(client_id, doc_id, request_user_id=None):
    """
    Async task to send document via email
    """
    from django.contrib.auth.models import User
    from apps.EasyDocs.models import Client, ClientDoc
    from apps.EasyDocs.files.documents import send_document_email
    
    try:
        client = Client.objects.get(id=client_id)
        doc = ClientDoc.objects.get(id=doc_id, client=client)
        
        # Create a mock request object for the task
        class MockRequest:
            def __init__(self, user_id):
                self.user = User.objects.get(id=user_id) if user_id else None
                self.META = {}
        
        request = MockRequest(request_user_id)
        success, message = send_document_email(request, client_id, doc_id)
        
        if success:
            logger.info(f"Email sent successfully for document {doc_id} to client {client_id}")
        else:
            logger.error(f"Failed to send email for document {doc_id}: {message}")
        
        return {'success': success, 'message': message}
        
    except Exception as e:
        logger.error(f"Email task failed for document {doc_id}: {e}")
        return {'success': False, 'message': str(e)}