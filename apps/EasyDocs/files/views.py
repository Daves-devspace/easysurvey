import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, FileResponse, Http404
from django.contrib import messages
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.core.files.storage import default_storage
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth import get_user_model
from apps.EasyDocs.models import DocType, ClientDoc, Document, Client, SiteSettings
from apps.EasyDocs.forms import DocTypeForm
from apps.EasyDocs.files.utils import get_drive_storage, log_audit
from django.contrib import messages
from email.utils import formataddr, parseaddr
from django.conf import settings
from django.core.mail import EmailMessage
import mimetypes
from apps.EasyDocs.files.documents import (
    upload_document_with_strategy, 
    download_document_content,
    delete_document_from_storage
)
from apps.EasyDocs.services.handoffs import (
    create_document_handoff,
    get_latest_handoffs_for_documents,
)

logger = logging.getLogger(__name__)
User = get_user_model()

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

    # ✅ FIX: Get doc_name from form (user-entered), NOT from filename
    doc_name = request.POST.get('doc_name', '').strip()
    
    # If user didn't enter a name, fallback to filename without extension
    if not doc_name:
        import os
        doc_name = os.path.splitext(uploaded_file.name)[0]
    
    # Check for duplicates using the user-entered doc_name
    if model == ClientDoc:
        client = instance_kwargs.get("client")
        from apps.EasyDocs.files.documents import check_document_exists
        if check_document_exists(client, doc_name):
            messages.error(request, f"A document with the name '{doc_name}' already exists for this client.")
            return None
    else:
        # For office documents, check by name
        if model.objects.filter(doc_name=doc_name).exists():
            messages.error(request, f"A document with the name '{doc_name}' already exists.")
            return None

    # Create document instance with user-entered doc_name
    try:
        doc = model(**instance_kwargs, doc_name=doc_name)  # ✅ Uses form doc_name
        doc.uploaded_by = request.user
        
        # Upload with unified strategy (this uses uploaded_file.name for the file path)
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
    Refined delete workflow - logs audit BEFORE deletion
    """
    try:
        doc_name = doc.doc_name
        doc_id = doc.pk  # Save before deletion
        backend = doc.storage_backend
        
        # Log audit BEFORE deleting (so doc.pk is still available)
        log_audit(
            request.user, 
            "delete_attempt", 
            doc, 
            request, 
            extra=f"Attempting to delete from {backend}"
        )
        
        # Delete from storage
        storage_success = delete_document_from_storage(doc)
        
        # Delete database record
        doc.delete()
        
        # Log success (doc is deleted, so we can't pass the instance)
        logger.info(f"Document {doc_id} '{doc_name}' deleted. Storage delete: {storage_success}")
        
        messages.success(request, f"'{doc_name}' deleted successfully.")
        
    except Exception as e:
        logger.error(f"Delete failed for doc {doc.id}: {e}", exc_info=True)
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
    
    return redirect(reverse("document_list"))

def download_office_document(request, doc_id):
    doc = get_object_or_404(Document, id=doc_id)
    return download_document_workflow(request, doc)

@require_POST
def delete_office_document(request, doc_id):
    doc = get_object_or_404(Document, id=doc_id)
    delete_document_workflow(request, doc)
    return redirect(reverse("document_list"))


@require_POST
def add_doctype(request):
    form = DocTypeForm(request.POST)
    if form.is_valid():
        form.save()
        messages.success(request, 'Document type added successfully.')
    else:
        messages.error(request, 'Failed to add document type. Please check the form.')
    return redirect(request.META.get('HTTP_REFERER', '/'))


@login_required
@require_POST
def assign_document_handoff(request):
    referer = request.META.get('HTTP_REFERER', reverse('document_list'))

    doc_kind = (request.POST.get('doc_kind') or '').strip().lower()
    document_id = request.POST.get('document_id')
    assigned_to_id = request.POST.get('assigned_to')
    notes = (request.POST.get('notes') or '').strip()

    if doc_kind not in {'office', 'client'}:
        messages.error(request, 'Invalid document type for handoff assignment.')
        return redirect(referer)

    if not document_id or not assigned_to_id:
        messages.error(request, 'Please select an assignee before submitting handoff.')
        return redirect(referer)

    if doc_kind == 'office':
        required_perm = 'easydocs.change_document'
        document = get_object_or_404(Document, pk=document_id)
        client = None
    else:
        required_perm = 'easydocs.change_clientdoc'
        document = get_object_or_404(ClientDoc, pk=document_id)
        client = document.client

    if not (request.user.is_superuser or request.user.has_perm(required_perm)):
        messages.error(request, 'You are not allowed to assign this document.')
        return redirect(referer)

    assigned_to = User.objects.filter(pk=assigned_to_id).first()
    if not assigned_to:
        messages.error(request, 'Selected assignee was not found.')
        return redirect(referer)

    result = create_document_handoff(
        document=document,
        assigned_to=assigned_to,
        assigned_by=request.user,
        client=client,
        notes=notes,
    )

    if result.get('success'):
        messages.success(request, result.get('message', 'Document handoff assigned successfully.'))
    else:
        messages.error(request, result.get('message', 'Failed to assign document handoff.'))

    return redirect(referer)


@login_required
def office_documents(request):
    query = request.GET.get('q', '')
    docs_qs = Document.objects.select_related('doc_type', 'uploaded_by').all().order_by('-uploaded_at')
    
    if query:
        docs_qs = docs_qs.filter(
            Q(doc_name__icontains=query) |
            Q(doc_type__name__icontains=query) |
            Q(location__icontains=query) |
            Q(reference__icontains=query)
        )

    docs = list(docs_qs)
    latest_handoffs = get_latest_handoffs_for_documents(docs)
    for doc in docs:
        doc.latest_handoff = latest_handoffs.get(doc.id)

    doc_types = DocType.objects.all()
    handoff_employees = User.objects.filter(employeeprofile__isnull=False).order_by('first_name', 'last_name', 'username')
    return render(request, "Management/documents.html", 
                 {
                     "documents": docs,
                     'doc_types': doc_types,
                     'query': query,
                     'handoff_employees': handoff_employees,
                 })





@require_POST
def email_client_document(request, client_id, doc_id):
    """
    Send a client document via email (works for both local and Drive storage).
    """
    client = get_object_or_404(Client, id=client_id)
    document = get_object_or_404(ClientDoc, id=doc_id)
    site_settings = SiteSettings.objects.first()

    referer = request.META.get("HTTP_REFERER")
    fallback = reverse("client_details", kwargs={"client_id": client_id})
    redirect_to = referer or fallback

    if not client.email:
        messages.error(request, "This client does not have an email address.")
        return redirect(redirect_to)

    raw_from = settings.DEFAULT_FROM_EMAIL or ""
    env_name, env_addr = parseaddr(raw_from)
    company_name = site_settings.company_name if site_settings else ""
    display_name = company_name or env_name or ""
    address = env_addr or env_name
    from_email = formataddr((display_name, address))

    subject = f"Your Document from {company_name or 'Our Company'}"
    tagline = site_settings.tagline or ""
    body_lines = [
        f"Hello {client.first_name},",
        "",
        "Please find your document attached.",
        tagline,
        "",
        "Best regards,",
        company_name,
    ]
    body = "\n".join([line for line in body_lines if line.strip()])

    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=from_email,
        to=[client.email],
    )

    try:
        file_content = document.get_file_content()
        if not file_content:
            messages.error(request, "Document file is unavailable for email (missing or not accessible).")
            return redirect(redirect_to)

        file_name = document.doc_name or "attachment"
        mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        email.attach(file_name, file_content, mime_type)
        logger.info(f"Attaching document {document.id} ({file_name}, {mime_type}) for client {client.id}")
    except Exception as e:
        logger.exception(f"Failed to attach document {document.id} for client {client.id}")
        messages.error(request, f"Could not attach the document file: {str(e)}")
        return redirect(redirect_to)

    try:
        email.send(fail_silently=False)
        messages.success(request, f"Document emailed successfully to {client.email}.")
        logger.info(f"Document {document.id} sent to client {client.id} ({client.email})")
    except Exception as e:
        logger.exception(f"Failed to send email to client {client.id} for document {document.id}")
        messages.error(request, f"Failed to send email: {str(e)}")

    return redirect(redirect_to)



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
    
    return redirect(reverse("document_list"))

