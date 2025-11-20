import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.EasyDocs.models import (
    Client, Service, ClientService,
    ClientSubService, LegalOfficePayout
)


class BulkPayToLegalTest(TestCase):
    def setUp(self):
        # Create a test client
        self.client_instance = Client.objects.create(
            first_name="John",
            last_name="Doe",
            email="john.doe@gmail.com",
            phone="0712345678"
        )

        # Create a service
        self.service = Service.objects.create(name="Legal Consultation")

        # Create a client service record
        self.client_service = ClientService.objects.create(
            client=self.client_instance,
            service=self.service,
            land_description="Land 123",
            status="active"
        )

        # Create a subservice
        self.subservice = ClientSubService.objects.create(
            client_service=self.client_service,
            sub_service="Will Drafting",
            paid_amount=Decimal("5000.00"),
            is_paid_to_legal_office=False
        )

    def test_bulk_pay_creates_payout_and_links_subservices(self):
        # Send POST request to simulate bulk payout
        response = self.client.post(
            reverse("bulk_pay_to_legal"),  # Ensure this matches your url name in urls.py
            data=json.dumps({
                "subservice_ids": [self.subservice.id]
            }),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")

        payout = LegalOfficePayout.objects.first()
        self.assertIsNotNone(payout)
        self.assertEqual(payout.total_amount, Decimal("5000.00"))
        self.assertEqual(payout.subservices.count(), 1)
        self.assertIn(self.subservice, payout.subservices.all())
