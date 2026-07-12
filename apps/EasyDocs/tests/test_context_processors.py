from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory

from apps.EasyDocs.context_processors import site_settings
from apps.EasyDocs.models import SiteSettings


@pytest.mark.django_db
def test_site_settings_uses_fallback_when_logo_file_is_missing():
    site = SiteSettings.objects.create(company_name="TestCo")
    site.logo = SimpleUploadedFile("logo.jpeg", b"fake", content_type="image/jpeg")
    site.save()

    request = RequestFactory().get("/")

    with patch("django.core.files.storage.FileSystemStorage.exists", return_value=False), patch(
        "django.core.files.storage.FileSystemStorage.get_modified_time",
        side_effect=AssertionError("missing logo should not require mtime lookup"),
    ) as mock_get_modified_time:
        ctx = site_settings(request)

    assert ctx["company_name"] == "TestCo"
    assert ctx["logo_url"].endswith("plotsync.png")
    mock_get_modified_time.assert_not_called()
