from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from apps.tenant_management.models import Tenant, Unit, Lease, Invoice
import logging

logger = logging.getLogger(__name__)

class TenantLeaseService:
    """
    Service class to encapsulate business logic for creating and ending leases.
    Ensures atomic operations, proper validation, and logging.
    """

    @staticmethod
    @transaction.atomic
    def create_tenant_with_lease(tenant_data, lease_data):
        """
        Creates a Tenant and a Lease in a single atomic transaction.

        1. Locks the Unit record to prevent race conditions.
        2. Validates that the unit is vacant and has no active lease.
        3. Creates the Tenant record.
        4. Creates the Lease record (marking is_active=True).
        5. Marks Unit.is_occupied = True and saves.
        6. If lease starts today or earlier, generate initial invoice.

        Returns a dict with success flag, objects, and message.
        """
        try:
            # Step 1: Lock and fetch unit
            unit = Unit.objects.select_for_update().get(id=lease_data['unit_id'])
            # Step 2: Check vacancy and active lease
            if unit.is_occupied:
                raise ValidationError("Selected unit is no longer available.")
            existing = Lease.objects.filter(unit=unit, is_active=True).first()
            if existing:
                raise ValidationError(f"Unit {unit.unit_number} already leased.")

            # Step 3: Create tenant
            tenant = Tenant.objects.create(**tenant_data)
            logger.info(f"Tenant created: {tenant.full_name} (ID {tenant.id})")

            # Step 4: Create lease
            lease = Lease.objects.create(
                tenant=tenant,
                unit=unit,
                start_date=lease_data['start_date'],
                deposit_amount=lease_data['deposit_amount'],
                is_active=True
            )
            logger.info(f"Lease created: {lease.id} for unit {unit.unit_number}")

            # Step 5: Mark unit occupied
            unit.is_occupied = True
            unit.save(update_fields=['is_occupied'])

            # Step 6: Generate invoice if start <= today
            if lease.start_date <= timezone.now().date():
                TenantLeaseService.generate_initial_invoice(lease)

            return {
                'success': True,
                'tenant': tenant,
                'lease': lease,
                'message': f"Tenant {tenant.full_name} and lease for unit {unit.unit_number} created."
            }
        except ValidationError:
            # Rethrow for view to catch and display
            raise
        except Exception as e:
            logger.error(f"Error in create_tenant_with_lease: {e}")
            raise Exception(f"Failed to create tenant/lease: {e}")

    @staticmethod
    def generate_initial_invoice(lease):
        """
        Generates the first monthly invoice for a new lease.

        - Invoice date = lease.start_date
        - Due date = +30 days
        - Rent amount from unit
        - Water/other charges set to 0 initially
        """
        invoice_date = lease.start_date
        due_date = invoice_date + timezone.timedelta(days=30)
        invoice = Invoice.objects.create(
            lease=lease,
            invoice_date=invoice_date,
            due_date=due_date,
            rent_amount=lease.unit.rent_amount,
            water_amount=0.0,
            other_charges=0.0,
            total_amount=lease.unit.rent_amount,
            is_paid=False,
            auto_generated=True
        )
        logger.info(f"Initial invoice {invoice.id} for lease {lease.id}")
        return invoice

    @staticmethod
    def get_available_units(property_id=None):
        """
        Returns a queryset of vacant units, optionally filtered by property.
        """
        qs = Unit.objects.filter(is_occupied=False).select_related('property')
        if property_id:
            qs = qs.filter(property_id=property_id)
        return qs.order_by('property__name', 'unit_number')

    @staticmethod
    @transaction.atomic
    def end_lease_and_free_unit(lease_id, end_date=None):
        """
        Ends a given lease and marks its unit as vacant.

        Steps:
        1. Lock and fetch lease
        2. Call Lease.end_lease() to set is_active=False
        3. Update unit.is_occupied=False
        """
        try:
            lease = Lease.objects.select_for_update().get(id=lease_id)
            unit = lease.unit
            # End lease
            lease.end_lease()
            # Free unit
            unit.is_occupied = False
            unit.save(update_fields=['is_occupied'])
            logger.info(f"Lease {lease_id} ended; unit {unit.unit_number} freed.")
            return {'success': True, 'message': f"Lease ended; unit {unit.unit_number} is now available."}
        except Lease.DoesNotExist:
            raise ValidationError("Lease not found.")
        except Exception as e:
            logger.error(f"Error ending lease {lease_id}: {e}")
            raise
