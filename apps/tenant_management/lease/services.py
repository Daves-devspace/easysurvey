import logging
from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from apps.tenant_management.models import Tenant, Lease, Deposit, Unit, MeterReading
# Import the InvoiceService to trigger billing on creation
from apps.tenant_management.services.invoice_service import InvoiceService

logger = logging.getLogger(__name__)

class TenantLeaseService:
    """
    Service to handle the orchestration of creating Tenants and Leases.
    Ensures data consistency and triggers initial billing.
    """

    @classmethod
    def save_tenant_with_lease(cls, tenant_data: dict, lease_data: dict, tenant_id=None, lease_id=None):
        """
        Create or update a tenant with an associated lease.
        
        Workflow:
        1. Create/Update Tenant.
        2. Create/Update Lease.
        3. Mark Unit as Occupied.
        4. Create Baseline Meter Reading (NEW).
        5. Generate the FIRST Invoice (Move-in Invoice: Deposit + Rent).
        """
        try:
            with transaction.atomic():
                # --- 1. Handle Tenant ---
                if tenant_id:
                    tenant = Tenant.objects.get(pk=tenant_id)
                    for field, value in tenant_data.items():
                        setattr(tenant, field, value)
                    tenant.save()
                    action = "updated"
                else:
                    tenant = Tenant.objects.create(**tenant_data)
                    action = "created"

                # --- 2. Handle Lease ---
                # Extract the initial reading from the payload so it doesn't break Lease creation
                initial_reading_val = lease_data.pop("initial_reading", None)
                
                if lease_id:
                    lease = Lease.objects.get(pk=lease_id, tenant=tenant)
                    old_unit = lease.unit
                    
                    for field, value in lease_data.items():
                        if field != "deposit_amount": 
                            setattr(lease, field, value)
                    lease.save()
                    
                    # If unit changed, update occupancy
                    if old_unit.id != lease.unit.id:
                        old_unit.is_occupied = False
                        old_unit.save(update_fields=['is_occupied'])
                        lease.unit.is_occupied = True
                        lease.unit.save(update_fields=['is_occupied'])
                        
                    lease_action = "updated"
                else:
                    # For new lease, ensure unit is free
                    unit = Unit.objects.select_for_update().get(pk=lease_data['unit_id'])
                    if unit.is_occupied:
                        raise ValidationError(f"Unit {unit.unit_number} is already occupied.")
                        
                    lease = Lease.objects.create(tenant=tenant, **lease_data)
                    
                    # Mark unit occupied
                    unit.is_occupied = True
                    unit.save(update_fields=['is_occupied'])
                    
                    lease_action = "created"

                # --- 3. Handle Deposit Object ---
                deposit_amount = lease_data.get("deposit_amount", Decimal('0.00'))
                if deposit_amount > 0:
                    Deposit.objects.get_or_create(
                        lease=lease,
                        defaults={
                            "tenant": tenant,
                            "amount": deposit_amount, 
                            "amount_held": Decimal('0.00')
                        }
                    )
                    
                # --- 4. Handle Baseline Meter Reading (FIXED) ---
                # We create a reading with 0 usage to act as the start point for the next calculation.
                if lease_action == "created" and initial_reading_val is not None:
                    # FIX: Set previous_reading = current_reading = initial_reading_val.
                    # Result: Usage = 0.
                    # This tells the system "This is a start point, not a bill".
                    MeterReading.objects.create(
                        unit=lease.unit,
                        reading_date=lease.start_date,
                        previous_reading=initial_reading_val, # Matches current so usage is 0
                        current_reading=initial_reading_val,
                        usage=Decimal('0.00'),
                        amount=Decimal('0.00'),
                    )

                # --- 5. Trigger Move-In Invoice ---
                # This generates the invoice for the start_date.
                if lease_action == "created":
                    InvoiceService.upsert_rent_invoice_line_for_lease(
                        lease=lease, 
                        billing_date=lease.start_date
                    )

                return {
                    "tenant": tenant,
                    "lease": lease,
                    "message": f"Tenant {tenant.full_name} added. Baseline reading set to {initial_reading_val}."
                }

        except ValidationError as e:
            raise
        except Exception as e:
            logger.exception("Unexpected error saving tenant/lease: %s", e)
            raise

    @classmethod
    def get_available_units(cls, property_id):
        """Return list of unoccupied units for a property."""
        return Unit.objects.filter(property_id=property_id, is_occupied=False)

    @classmethod
    def end_lease_and_free_unit(cls, lease_id):
        """End a lease and mark the unit as vacant."""
        try:
            with transaction.atomic():
                lease = Lease.objects.select_for_update().get(pk=lease_id)
                lease.end_lease() # Sets is_active=False
                
                if lease.unit:
                    lease.unit.is_occupied = False
                    lease.unit.save(update_fields=['is_occupied'])
                    
                return {"success": True, "message": f"Lease ended for {lease.tenant.full_name}"}
        except Lease.DoesNotExist:
            return {"success": False, "message": "Lease not found"}