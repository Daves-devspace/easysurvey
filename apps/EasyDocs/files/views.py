import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, FileResponse, Http404
from django.contrib import messages
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.core.files.storage import default_storage

from apps.EasyDocs.models import DocType, ClientDoc, Document, Client, SiteSettings
from apps.EasyDocs.forms import DocTypeForm
from apps.EasyDocs.files.utils import get_drive_storage, log_audit
from apps.EasyDocs.files.documents import (
    upload_document_with_strategy, 
    download_document_content,
    delete_document_from_storage
)

logger = logging.getLogger(__name__)

# ----------------------------
# CORE UPLOAD LOGIC - REFINED
# ----------------------------
def upload_document_workflow(request, model, instance_kwargs, allowed_mimes, document_type):
    """
    Refined upload workflow with unified storage strategy
    """
    uploaded_file = request.FILES.get('doc_file')
    if not uploaded_file:
        messages.error(request, "No file uploaded.")
        return None

    # Validate file
    try:
        from apps.EasyDocs.models import validate_file_size, validate_mime
        validate_file_size(uploaded_file)
        validate_mime(uploaded_file, allowed_mimes)
    except ValidationError as e:
        messages.error(request, f"Invalid file: {e}")
        return None

    # Check for duplicates
    doc_name = uploaded_file.name
    if model == ClientDoc:
        client = instance_kwargs.get("client")
        from apps.EasyDocs.files.documents import check_document_exists
        if check_document_exists(client, doc_name):
            messages.error(request, "A document with this name already exists.")
            return None
    else:
        if model.objects.filter(doc_name=doc_name).exists():
            messages.error(request, "A document with this name already exists.")
            return None

    # Create document instance
    try:
        doc = model(**instance_kwargs, doc_name=doc_name)
        doc.uploaded_by = request.user
        
        # Upload with unified strategy
        success = upload_document_with_strategy(doc, uploaded_file)
        
        if success:
            messages.success(request, f"'{doc_name}' uploaded successfully.")
            log_audit(request.user, "upload", doc, request, 
                     extra=f"Storage: {doc.storage_backend}")
            return doc
        else:
            messages.error(request, f"Failed to upload '{doc_name}'.")
            return None
            
    except Exception as e:
        logger.error(f"Upload workflow failed: {e}")
        messages.error(request, f"Upload failed: {str(e)}")
        return None

def download_document_workflow(request, doc, as_attachment=True):
    """
    Refined download workflow
    """
    try:
        content = download_document_content(doc)
        if not content:
            messages.error(request, "File not available in storage.")
            log_audit(request.user, "download_failed", doc, request, 
                     extra="File not found")
            raise Http404("File not found")

        # Create response
        response = HttpResponse(content, content_type='application/octet-stream')
        disposition = 'attachment' if as_attachment else 'inline'
        response['Content-Disposition'] = f'{disposition}; filename="{doc.doc_name}"'
        
        log_audit(request.user, "download", doc, request, 
                 extra=f"From: {doc.storage_backend}")
        return response
        
    except Exception as e:
        logger.error(f"Download failed for doc {doc.id}: {e}")
        messages.error(request, "Download failed.")
        log_audit(request.user, "download_failed", doc, request, extra=str(e))
        raise Http404("Download failed")

def delete_document_workflow(request, doc):
    """
    Refined delete workflow
    """
    try:
        doc_name = doc.doc_name
        
        # Delete from storage
        storage_success = delete_document_from_storage(doc)
        
        # Delete database record
        doc.delete()
        
        messages.success(request, f"'{doc_name}' deleted successfully.")
        log_audit(request.user, "delete", doc, request, 
                 extra=f"Storage delete: {storage_success}")
        
    except Exception as e:
        logger.error(f"Delete failed for doc {doc.id}: {e}")
        messages.error(request, f"Delete failed: {str(e)}")
        raise

# ----------------------------
# CLIENT DOCUMENT VIEWS - REFINED
# ----------------------------
@require_POST
def upload_client_document(request, client_id):
    client = get_object_or_404(Client, id=client_id)
    doc_type_id = request.POST.get("doc_type")
    
    if not doc_type_id:
        messages.error(request, "Document type is required.")
        return redirect(reverse("client_documents", kwargs={"client_id": client_id}))

    try:
        doc_type = DocType.objects.get(id=doc_type_id)
    except DocType.DoesNotExist:
        messages.error(request, "Invalid document type selected.")
        return redirect(reverse("client_documents", kwargs={"client_id": client_id}))

    doc = upload_document_workflow(
        request=request,
        model=ClientDoc,
        instance_kwargs={"client": client, "doc_type": doc_type},
        allowed_mimes=[
            'application/pdf', 'image/jpeg', 'image/png', 
            'application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        ],
        document_type="client"
    )

    return redirect(reverse("client_details", kwargs={"client_id": client_id}))

def download_client_document(request, doc_id):
    doc = get_object_or_404(ClientDoc, id=doc_id)
    return download_document_workflow(request, doc)

@require_POST
def delete_client_document(request, client_id, doc_id):
    client = get_object_or_404(Client, id=client_id)
    doc = get_object_or_404(ClientDoc, id=doc_id, client=client)
    
    delete_document_workflow(request, doc)
    return redirect(reverse("client_details", kwargs={"client_id": client_id}))

# ----------------------------
# OFFICE DOCUMENT VIEWS - REFINED
# ----------------------------
@require_POST
def upload_office_document(request):
    doc_type_id = request.POST.get("doc_type")
    doc_type = None
    
    if doc_type_id:
        try:
            doc_type = DocType.objects.get(id=doc_type_id)
        except DocType.DoesNotExist:
            messages.error(request, "Invalid document type selected.")

    doc = upload_document_workflow(
        request=request,
        model=Document,
        instance_kwargs={
            "doc_type": doc_type,
            "location": request.POST.get("location", "Office"),
            "reference": request.POST.get("reference", "AUTO")
        },
        allowed_mimes=[
            'application/pdf', 'image/jpeg', 'image/png', 
            'application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'text/plain', 'application/vnd.ms-excel',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        ],
        document_type="office"
    )
    
    return redirect(reverse("office_documents"))

def download_office_document(request, doc_id):
    doc = get_object_or_404(Document, id=doc_id)
    return download_document_workflow(request, doc)

@require_POST
def delete_office_document(request, doc_id):
    doc = get_object_or_404(Document, id=doc_id)
    delete_document_workflow(request, doc)
    return redirect(reverse("office_documents"))


@require_POST
def add_doctype(request):
    form = DocTypeForm(request.POST)
    if form.is_valid():
        form.save()
        messages.success(request, 'Document type added successfully.')
    else:
        messages.error(request, 'Failed to add document type. Please check the form.')
    return redirect(request.META.get('HTTP_REFERER', '/'))


def office_documents(request):
    query = request.GET.get('q', '')
    docs = Document.objects.all().order_by('-uploaded_at')
    
    if query:
        docs = docs.filter(
            Q(doc_name__icontains=query) |
            Q(doc_type__name__icontains=query) |
            Q(location__icontains=query) |
            Q(reference__icontains=query)
        )

    doc_types = DocType.objects.all()
    return render(request, "Management/documents.html", 
                 {"documents": docs, 'doc_types': doc_types, 'query': query})




@require_POST
def email_client_document(request, client_id, doc_id):
    """
    Send client document via email - handles both local and Drive storage
    """
    from apps.EasyDocs.files.documents import send_document_email
    
    try:
        success, message = send_document_email(request, client_id, doc_id)
        
        if success:
            messages.success(request, message)
        else:
            messages.error(request, message)
            
    except Exception as e:
        logger.error(f"Email document failed: {e}")
        messages.error(request, f"Failed to send email: {str(e)}")
    
    return redirect(reverse("client_details", kwargs={"client_id": client_id}))


@require_POST
def migrate_client_documents_to_drive(request, client_id):
    """
    Migrate all client documents to Drive
    """
    from apps.EasyDocs.files.tasks import migrate_documents_to_drive_task
    
    client = get_object_or_404(Client, id=client_id)
    
    try:
        # Trigger async migration task
        task = migrate_documents_to_drive_task.delay()
        messages.info(request, f"Migration task started for {client.first_name}'s documents. Task ID: {task.id}")
        
    except Exception as e:
        logger.error(f"Failed to start migration task: {e}")
        messages.error(request, f"Failed to start migration: {str(e)}")
    
    return redirect(reverse("client_details", kwargs={"client_id": client_id}))

@require_POST
def migrate_all_documents_to_drive(request):
    """
    Migrate all documents to Drive (admin function)
    """
    from apps.EasyDocs.files.tasks import migrate_documents_to_drive_task
    
    try:
        # Trigger async migration task
        task = migrate_documents_to_drive_task.delay()
        messages.info(request, f"Full document migration started. Task ID: {task.id}")
        
    except Exception as e:
        logger.error(f"Failed to start full migration task: {e}")
        messages.error(request, f"Failed to start migration: {str(e)}")
    
    return redirect(reverse("office_documents"))

