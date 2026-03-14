from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.EasyDocs.accounts.allocations import allocate_payment_shares
from apps.EasyDocs.accounts.revenue import get_revenue_from_payments
from apps.EasyDocs.models import (
    Client,
    ClientService,
    ClientServiceProcess,
    ClientSubService,
    Payment,
    Process,
    Service,
    ServiceCategory,
    SubService,
)


class RevenueAllocationConsistencyTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(
            first_name="Revenue",
            last_name="Consistency",
            phone="0701002003",
        )

    def test_allocate_payment_shares_falls_back_to_process_cost_when_no_override(self):
        service = Service.objects.create(
            name="Title Allocation Fallback",
            category=ServiceCategory.TITLE,
            total_price=Decimal("500.00"),
        )
        process = Process.objects.create(
            service=service,
            name="Verification",
            step_order=1,
            cost=Decimal("500.00"),
            message="Step",
            notification_enabled=False,
        )
        client_service = ClientService.objects.create(
            client=self.client_obj,
            service=service,
            land_description="Plot A",
        )

        step = ClientServiceProcess.objects.get(
            client_service=client_service,
            process=process,
        )
        step.overridden_cost = None
        step.paid_amount = Decimal("0.00")
        step.save(update_fields=["overridden_cost", "paid_amount"])

        payment = Payment(
            client_service=client_service,
            amount=Decimal("300.00"),
            payment_method="cash",
        )

        allocations = allocate_payment_shares(
            payment,
            service_processes=[step],
            sub_services=[],
        )

        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0]["target_type"], "service_step")
        self.assertEqual(allocations[0]["gross"], Decimal("300.00"))

    def test_revenue_keeps_main_component_for_mixed_payment(self):
        service = Service.objects.create(
            name="Title Mixed Payment",
            category=ServiceCategory.TITLE,
            total_price=Decimal("700.00"),
        )
        process = Process.objects.create(
            service=service,
            name="Search",
            step_order=1,
            cost=Decimal("700.00"),
            message="Search",
            notification_enabled=False,
        )
        client_service = ClientService.objects.create(
            client=self.client_obj,
            service=service,
            land_description="Plot B",
        )

        step = ClientServiceProcess.objects.get(
            client_service=client_service,
            process=process,
        )
        step.overridden_cost = Decimal("700.00")
        step.save(update_fields=["overridden_cost"])

        legal_stamp = SubService.objects.create(
            name="Legal Stamp",
            department=SubService.RoleChoices.LEGAL,
            price=Decimal("300.00"),
        )
        ClientSubService.objects.create(
            client_service=client_service,
            sub_service=legal_stamp,
            overridden_price=Decimal("300.00"),
        )

        client_service.update_full_total()

        payment = Payment.objects.create(
            client_service=client_service,
            amount=Decimal("1000.00"),
            payment_method="cash",
        )

        report_day = timezone.localdate(payment.payment_date)
        report = get_revenue_from_payments(start_date=report_day, end_date=report_day)

        self.assertEqual(report["gross_inflow"], Decimal("1000.00"))
        self.assertEqual(report["gross_total"], Decimal("1000.00"))
        self.assertEqual(report["main_services"]["gross_total"], Decimal("700.00"))
        self.assertEqual(report["subservices"]["gross_total"], Decimal("300.00"))
