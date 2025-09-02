import logging
from django.db import transaction
from django.core.exceptions import ValidationError
from apps.tenant_management.models import Tenant, Lease, Deposit, LedgerEntry

logger = logging.getLogger(__name__)


class TenantLeaseService:

    @classmethod
    def save_tenant_with_lease(cls, tenant_data: dict, lease_data: dict, tenant_id=None, lease_id=None):
        """
        Create or update a tenant with an associated lease and deposit.
        - If tenant_id is None → creates a new tenant
        - If lease_id is None → creates a new lease
        - Handles deposit creation/update
        """
        try:
            with transaction.atomic():
                # Tenant
                if tenant_id:
                    tenant = Tenant.objects.get(pk=tenant_id)
                    for field, value in tenant_data.items():
                        setattr(tenant, field, value)
                    tenant.save()
                    action = "updated"
                else:
                    tenant = Tenant.objects.create(**tenant_data)
                    action = "created"

                # Lease
                if lease_id:
                    lease = Lease.objects.get(pk=lease_id, tenant=tenant)
                    for field, value in lease_data.items():
                        if field != "deposit_amount":
                            setattr(lease, field, value)
                    lease.save()
                    lease_action = "updated"
                else:
                    lease = Lease.objects.create(tenant=tenant, **lease_data)
                    lease_action = "created"

                # Deposit (same as before)
                deposit_amount = lease_data.get("deposit_amount", 0)
                if deposit_amount > 0:
                    deposit, created = Deposit.objects.get_or_create(
                        lease=lease,
                        defaults={"amount": deposit_amount, "amount_held": deposit_amount},
                    )
                    if not created:
                        deposit.amount = deposit_amount
                        deposit.amount_held = deposit_amount
                        deposit.save()

                    LedgerEntry.objects.update_or_create(
                        lease=lease,
                        description="Deposit received",
                        defaults={"debit": deposit_amount}
                    )

                return {
                    "tenant": tenant,
                    "lease": lease,
                    "message": f"Tenant {tenant.full_name} and lease {lease_action} successfully."
                }

        except ValidationError as e:
            raise
        except Exception as e:
            logger.exception("Unexpected error saving tenant/lease: %s", e)
            raise
