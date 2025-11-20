from celery import shared_task
import logging
from apps.EasyDocs.models import ClientDoc,Document
from apps.EasyDocs.files.documents import migrate_document_to_drive

logger = logging.getLogger(__name__)

BATCH_SIZE = 50  # Number of documents per batch (adjust as needed)

@shared_task(bind=True, max_retries=3)
def migrate_documents_to_drive_task(self):
    """
    Celery task to migrate documents to Drive in batches.
    Prevents memory bloat and respects Drive API limits.
    """
    try:
        # Query documents not fully on Drive
        local_client_docs_qs = ClientDoc.objects.filter(storage_backend__in=['local', 'hybrid']).order_by('id')
        local_office_docs_qs = Document.objects.filter(storage_backend__in=['local', 'hybrid']).order_by('id')

        total_docs = local_client_docs_qs.count() + local_office_docs_qs.count()
        logger.info(f"🚀 Starting migration task: {total_docs} documents found")

        success_count = 0
        processed_count = 0

        # Combine querysets for batching
        combined_qs = list(local_client_docs_qs) + list(local_office_docs_qs)

        # Process in batches
        for i in range(0, len(combined_qs), BATCH_SIZE):
            batch_docs = combined_qs[i:i+BATCH_SIZE]
            logger.info(f"🔹 Processing batch {i//BATCH_SIZE + 1} ({len(batch_docs)} documents)")

            # Sequential processing per batch to avoid hitting API rate limits
            for doc in batch_docs:
                success, message = migrate_document_to_drive(doc)
                processed_count += 1
                if success:
                    success_count += 1
                    logger.info(f"✅ Document {doc.id} migrated successfully: {message}")
                else:
                    logger.warning(f"⚠️ Document {doc.id} migration skipped/failed: {message}")

            # Optional: sleep briefly between batches to respect API limits
            # import time
            # time.sleep(2)

        logger.info(f"🏁 Migration task completed: {success_count}/{processed_count} documents migrated")

        return {
            "success_count": success_count,
            "total_count": processed_count,
            "message": f"Migrated {success_count} of {processed_count} documents to Drive"
        }

    except Exception as exc:
        logger.exception(f"🔥 Migration task encountered an unexpected error: {exc}")
        # Retry after 5 minutes for transient issues
        raise self.retry(exc=exc, countdown=300)
    
    
    


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