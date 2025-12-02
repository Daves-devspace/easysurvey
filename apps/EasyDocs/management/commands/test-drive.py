import io
import logging
from django.core.management.base import BaseCommand
from django.core.files.base import ContentFile

from apps.EasyDocs.files.storage_backends import GoogleDriveStorage

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Verify Google Drive integration by uploading and downloading a test file"

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("🔍 Starting Google Drive integration test..."))

        try:
            # Initialize storage (this logs which credential source is used)
            storage = GoogleDriveStorage()

            # Prepare a test file
            test_filename = "test_drive_integration.txt"
            test_content = ContentFile(b"Hello from EasyDocs test_drive command!")

            self.stdout.write("📤 Uploading test file...")
            file_id = storage._save(test_filename, test_content)

            self.stdout.write(self.style.SUCCESS(f"✅ Uploaded file, ID={file_id}"))

            # Download it back
            self.stdout.write("📥 Downloading test file back...")
            downloaded_file = storage._open(file_id)

            content = downloaded_file.read().decode("utf-8")
            self.stdout.write(self.style.SUCCESS(f"✅ Downloaded content: {content}"))

            # Clean up
            self.stdout.write("🗑️ Deleting test file...")
            storage.delete(file_id)

            self.stdout.write(self.style.SUCCESS("🎉 Google Drive integration test PASSED!"))

        except Exception as e:
            logger.exception("Google Drive test failed")
            self.stderr.write(self.style.ERROR(f"❌ Google Drive integration test FAILED: {e}"))
