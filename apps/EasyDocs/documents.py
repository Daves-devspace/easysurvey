from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist
from django.conf import settings
from django.core.files.storage import default_storage
import os

from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from apps.EasyDocs.forms import DocTypeForm, ClientDocumentForm
from apps.EasyDocs.models import ClientDoc, Client


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



def handle_document_upload(request, client):
    if request.method == 'POST' and 'upload_doc' in request.POST:
        doc_form = ClientDocumentForm(request.POST, request.FILES)
        if doc_form.is_valid():
            new_doc = doc_form.save(commit=False)
            new_doc.client = client
            new_doc.uploaded_by = request.user
            new_doc.save()
            messages.success(request, "Document uploaded successfully.")
        else:
            messages.error(request, "Failed to upload document. Please check the form.")


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
