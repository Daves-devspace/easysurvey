import pytest
from django.test import TestCase
from django.http import QueryDict
from django.utils import timezone
import os
import django
from django.urls import reverse
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "GGI.settings")
django.setup()
from apps.EasyDocs.models import Service, Process, ClientService, ClientServiceProcess, ServiceCategory, Client
from apps.EasyDocs.services.services import apply_client_service_logic



@pytest.mark.django_db
def test_edit_client_service_syncs_processes(client):
    """
    System-level test:
    - Service has 2 processes
    - We create a ClientService
    - Then simulate editing it via POST with only 1 process selected
    - Expect helper to sync CSP rows accordingly
    """

    # 1. Setup initial objects
    client_obj = Client.objects.create(
        first_name="ACME",
        last_name="Corp",
        email="acme@example.com",
        phone="0700000000"
    )

    service = Service.objects.create(name="Registration", category=ServiceCategory.TITLE)

    # ✅ add step_order to satisfy NOT NULL constraint
    p1 = Process.objects.create(service=service, name="Step 1", cost=100, step_order=1)
    p2 = Process.objects.create(service=service, name="Step 2", cost=200, step_order=2)

    cs = ClientService.objects.create(client=client_obj, service=service)

    # 2. Initial sync: both processes should exist
    cs.service_processes.get_or_create(process=p1)
    cs.service_processes.get_or_create(process=p2)
    assert cs.service_processes.count() == 2

    # 3. Simulate editing with only p1 selected
    url = reverse("edit_client_service", args=[client_obj.id])
    response = client.post(url, {
        "client_service_id": cs.id,
        "service": service.id,
        "process_id[]": [p1.id],   # drop p2
    })

    assert response.status_code == 200, response.content

    cs.refresh_from_db()
    proc_ids = list(cs.service_processes.values_list("process_id", flat=True))
    assert proc_ids == [p1.id]  # ✅ only p1 remains


@pytest.mark.parametrize(
    "is_new, posted_pids, expected_pids",
    [
        # Case 1: Creating new → should take ALL service processes
        (True, [], "all"),
        # Case 2: Updating → should only keep posted subset
        (False, ["keep_p1"], "subset"),
    ]
)
@pytest.mark.django_db
def test_apply_client_service_logic_parametrized(is_new, posted_pids, expected_pids):
    """
    Unit-level test of apply_client_service_logic:
    - Case 1 (is_new=True): syncs all service processes
    - Case 2 (is_new=False): syncs only the posted subset
    """

    client_obj = Client.objects.create(
        first_name="ACME",
        last_name="Corp",
        email="acme@example.com",
        phone="0700000000"
    )
    service = Service.objects.create(name="Registration", category=ServiceCategory.TITLE)

    p1 = Process.objects.create(service=service, name="Step 1", cost=100, step_order=1)
    p2 = Process.objects.create(service=service, name="Step 2", cost=200, step_order=2)

    cs = ClientService.objects.create(client=client_obj, service=service)

    # Initial CSPs (both)
    cs.service_processes.get_or_create(process=p1)
    cs.service_processes.get_or_create(process=p2)

    # Build post_data
    post_data = QueryDict(mutable=True)
    if "keep_p1" in posted_pids:
        post_data.setlist("process_id[]", [str(p1.id)])

    # Call helper
    apply_client_service_logic(cs, service, post_data=post_data, is_new=is_new)
    proc_ids = set(cs.service_processes.values_list("process_id", flat=True))

    if expected_pids == "all":
        assert proc_ids == {p1.id, p2.id}
    elif expected_pids == "subset":
        assert proc_ids == {p1.id}


    
    
# @pytest.mark.django_db
# class TestClientServiceLogic:
#     @pytest.fixture
#     def client(self):
#         return Client.objects.create(
#             first_name="Test",
#             last_name="Client",
#             email="client@example.com",
#             phone="0712345678"
#         )

#     @pytest.fixture
#     def service_with_processes(self):
#         """Service with 2 processes"""
#         service = Service.objects.create(
#             name="Title Deed Service",
#             category=ServiceCategory.TITLE,
#             total_price=0
#         )
#         p1 = Process.objects.create(
#             service=service,
#             name="Step 1",
#             description="First step",
#             step_order=1,
#             cost=100,
#             message="Step 1 message"
#         )
#         p2 = Process.objects.create(
#             service=service,
#             name="Step 2",
#             description="Second step",
#             step_order=2,
#             cost=200,
#             message="Step 2 message"
#         )
#         return service, [p1, p2]

#     @pytest.fixture
#     def other_service_with_process(self):
#         """Different Service with 1 process"""
#         service = Service.objects.create(
#             name="Different Service",
#             category=ServiceCategory.TITLE,
#             total_price=0
#         )
#         p3 = Process.objects.create(
#             service=service,
#             name="Only step",
#             description="Single",
#             step_order=1,
#             cost=300,
#             message="Only step message"
#         )
#         return service, [p3]

#     @pytest.fixture
#     def service_no_processes(self):
#         """Service with no processes at all"""
#         return Service.objects.create(
#             name="Empty Service",
#             category=ServiceCategory.TITLE,
#             total_price=0
#         )

#     @pytest.mark.parametrize("scenario", ["fresh_create", "update_service", "empty_service"])
#     def test_process_sync_logic(
#         self, client, service_with_processes, other_service_with_process, service_no_processes, scenario
#     ):
#         service, processes = service_with_processes
#         cs = ClientService.objects.create(
#             client=client,
#             service=service,
#             land_description="Plot 123"
#         )

#         if scenario == "fresh_create":
#             apply_client_service_logic(cs, service, is_new=True)

#             proc_ids = set(cs.service_processes.values_list("process_id", flat=True))
#             expected_ids = {p.id for p in processes}
#             assert proc_ids == expected_ids, f"Expected {expected_ids}, got {proc_ids}"

#         if scenario == "update_service":
#             # Seed with initial processes
#             apply_client_service_logic(cs, service, is_new=True)

#             # Reassign to a new service with 1 process
#             other_service, other_processes = other_service_with_process
#             cs.service = other_service
#             cs.save(update_fields=["service"])

#             apply_client_service_logic(cs, other_service, is_new=False)

#             proc_ids = set(cs.service_processes.values_list("process_id", flat=True))
#             expected_ids = {p.id for p in other_processes}
#             assert proc_ids == expected_ids, f"Expected {expected_ids}, got {proc_ids}"

#         if scenario == "empty_service":
#             # Seed with processes
#             apply_client_service_logic(cs, service, is_new=True)
#             assert cs.service_processes.exists(), "Setup failed: CSPs should exist before switching"

#             # Switch to empty service
#             cs.service = service_no_processes
#             cs.save(update_fields=["service"])

#             apply_client_service_logic(cs, service_no_processes, is_new=False)

#             assert not cs.service_processes.exists(), "Expected no CSPs after switching to empty service"