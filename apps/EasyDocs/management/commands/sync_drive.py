from django.core.management.base import BaseCommand
from apps.EasyDocs.files.tasks import sync_pending_documents

class Command(BaseCommand):
    help = 'Sync pending documents to Google Drive'
    
    def handle(self, *args, **options):
        self.stdout.write('Starting Drive sync...')
        sync_pending_documents.delay()
        self.stdout.write('Drive sync queued successfully')