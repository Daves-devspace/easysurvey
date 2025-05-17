from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist
from django.conf import settings
from django.core.files.storage import default_storage
import os

from django.core.mail import send_mail, EmailMessage
from django.db.models import Q
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.EasyDocs.forms import DocTypeForm, ClientDocumentForm, DocumentForm
from apps.EasyDocs.models import ClientDoc, Client, SiteSettings, DocType, Document



def add_document(request):
    if request.method == "POST":
        form = DocumentForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, "Document added successfully!")
        else:
            messages.error(request, "There was an error adding the document. Please check the form.")

    # Always redirect back to the referring page or fallback to document list
    referer = request.META.get('HTTP_REFERER', reverse('document_list'))
    return redirect(referer)


def document_list(request):
    query = request.GET.get('q', '')
    documents = Document.objects.all()

    if query:
        documents = documents.filter(
            Q(doc_name__icontains=query) |
            Q(doc_type__name__icontains=query) |
            Q(location__icontains=query) |
            Q(reference__icontains=query)
        )

    doc_types = DocType.objects.all()

    return render(request, 'Management/documents.html', {
        'documents': documents,
        'doc_types': doc_types,
        'query': query,
    })



def add_doctype(request):
    if request.method == 'POST':
        form = DocTypeForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Document type added successfully.')
        else:
            messages.error(request, 'Failed to add document type. Please check the form.')
    return redirect(request.META.get('HTTP_REFERER', '/'))


# Utility functions for document management for a specific client


def upload_client_doc(request, client_id):
    if request.method == 'POST':
        client = get_object_or_404(Client, id=client_id)
        form = ClientDocumentForm(request.POST, request.FILES)

        if form.is_valid():
            doc = form.save(commit=False)
            doc.client = client
            doc.uploaded_by = request.user
            doc.save()
            messages.success(request, "Document uploaded successfully.")
        else:
            messages.error(request, "Failed to upload document. Please check the form.")

    referer = request.META.get('HTTP_REFERER', reverse('client_details', args=[client_id]))
    return redirect(referer)


def get_documents_for_client(client):
    """
    Retrieve all documents associated with a specific client.
    """
    return ClientDoc.objects.filter(client=client)


def get_document_by_name(client, doc_name):
    """
    Retrieve a document by its name for a specific client.
    """
    try:
        return ClientDoc.objects.get(client=client, doc_name=doc_name)
    except ObjectDoesNotExist:
        return None


def delete_document(client, doc_name):
    """
    Delete a specific document for a client by document name.
    """
    try:
        client_doc = ClientDoc.objects.get(client=client, doc_name=doc_name)
        # Optionally, you could delete the file from the storage as well.
        file_path = client_doc.doc_file.path
        if os.path.exists(file_path):
            default_storage.delete(file_path)
        client_doc.delete()
        return True
    except ObjectDoesNotExist:
        return False


def check_document_exists(client, doc_name):
    """
    Check if a document exists for a specific client.
    """
    return ClientDoc.objects.filter(client=client, doc_name=doc_name).exists()


def get_all_documents_of_type(doc_type):
    """
    Get all documents of a specific document type.
    """
    return ClientDoc.objects.filter(doc_type=doc_type)


@require_POST
def delete_document(request, client_id, doc_id):
    client = get_object_or_404(Client, id=client_id)
    document = get_object_or_404(ClientDoc, id=doc_id, client=client)

    # Delete the file from the server (optional)
    if document.doc_file:
        if os.path.isfile(document.doc_file.path):
            os.remove(document.doc_file.path)

    document.delete()

    # Use referrer to go back to the previous page
    referer = request.META.get('HTTP_REFERER', f'/client/{client.id}/')
    return HttpResponseRedirect(referer)




def send_doc_email_to_client(request, client_id, doc_id):
    client = get_object_or_404(Client, id=client_id)
    document = get_object_or_404(ClientDoc, id=doc_id)
    site_settings = SiteSettings.objects.first()

    if not client.email:
        messages.error(request, "This client does not have an email address.")
        return redirect(request.META.get('HTTP_REFERER', '/'))

    from_email = f"ValueTech Admin <{settings.DEFAULT_FROM_EMAIL}>"
    subject = f"Document from {site_settings.company_name if site_settings else 'Our Company'}"
    tagline = site_settings.tagline if site_settings and site_settings.tagline else "Thank you for letting us serve you!"

    message = f"""
    Hello {client.first_name},

    Please find your document attached.

    {tagline}
    """

    email = EmailMessage(subject, message, from_email, [client.email])

    # Attach file
    if document.doc_file:
        email.attach(document.doc_file.name, document.doc_file.read(), document.doc_file.mime_type)

    try:
        email.send()
        messages.success(request, f"Email with attachment successfully sent to {client.email}")
    except Exception as e:
        messages.error(request, f"Failed to send email: {str(e)}")

    return redirect(request.META.get('HTTP_REFERER', '/'))