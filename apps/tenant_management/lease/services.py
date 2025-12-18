import logging
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from apps.tenant_management.models import Tenant, Lease, Deposit, Unit, MeterReading, Invoice, Payment, LedgerEntry, TenantBalance
from apps.tenant_management.services.invoice_service import InvoiceService
from apps.tenant_management.services.payment_service import PaymentService
from apps.tenant_management.helpers.money_helpers import quantize_money as q

logger = logging.getLogger(__name__)

class TenantLeaseService:
    @classmethod
    def save_tenant_with_lease(cls, tenant_data: dict, lease_data: dict, tenant_id=None, lease_id=None):
        try:
            with transaction.atomic():
                if tenant_id:
                    tenant = Tenant.objects.get(pk=tenant_id)
                    for field, value in tenant_data.items(): setattr(tenant, field, value)
                    tenant.save()
                    action = "updated"
                else:
                    tenant = Tenant.objects.create(**tenant_data)
                    action = "created"

                initial_reading_val = lease_data.pop("initial_reading", None)
                
                if lease_id:
                    lease = Lease.objects.get(pk=lease_id, tenant=tenant)
                    old_unit = lease.unit
                    for field, value in lease_data.items():
                        if field != "deposit_amount": setattr(lease, field, value)
                    lease.save()
                    if old_unit.id != lease.unit.id:
                        old_unit.is_occupied = False; old_unit.save(update_fields=['is_occupied'])
                        lease.unit.is_occupied = True; lease.unit.save(update_fields=['is_occupied'])
                    lease_action = "updated"
                else:
                    unit = Unit.objects.select_for_update().get(pk=lease_data['unit_id'])
                    if unit.is_occupied: raise ValidationError(f"Unit {unit.unit_number} is already occupied.")
                    lease = Lease.objects.create(tenant=tenant, **lease_data)
                    unit.is_occupied = True; unit.save(update_fields=['is_occupied'])
                    lease_action = "created"

                deposit_amount = lease_data.get("deposit_amount", Decimal('0.00'))
                if deposit_amount > 0:
                    Deposit.objects.get_or_create(lease=lease, defaults={"tenant": tenant, "amount": deposit_amount, "amount_held": Decimal('0.00')})
                    
                if lease_action == "created" and initial_reading_val is not None:
                    MeterReading.objects.create(unit=lease.unit, reading_date=lease.start_date, previous_reading=initial_reading_val, current_reading=initial_reading_val, usage=Decimal('0.00'), amount=Decimal('0.00'))

                if lease_action == "created":
                    today = timezone.now().date()
                    start_period = lease.start_date.replace(day=1)
                    current_period = today.replace(day=1)
                    billing_date = today if start_period < current_period else lease.start_date
                    
                    # 1. Generate Invoice
                    invoice = InvoiceService.upsert_rent_invoice_line_for_lease(lease=lease, billing_date=billing_date)
                    logger.info(f"[Lease Creation] Generated Invoice #{invoice.id} for Lease {lease.id}. Initial Balance: {invoice.balance}")

                    # 2. Auto-Apply Credit
                    logger.info(f"[Lease Creation] Triggering credit application for Tenant {tenant.id}...")
                    PaymentService.apply_credit_to_invoice(tenant, invoice)
                    
                    # 3. Verify Result
                    invoice.refresh_from_db()
                    logger.info(f"[Lease Creation] Finished credit application. Invoice #{invoice.id} Final Balance: {invoice.balance}")

                return {"tenant": tenant, "lease": lease, "message": f"Tenant {tenant.full_name} added successfully."}
        except ValidationError as e: raise
        except Exception as e:
            logger.exception("Unexpected error saving tenant/lease: %s", e)
            raise

    @classmethod
    def get_available_units(cls, property_id):
        return Unit.objects.filter(property_id=property_id, is_occupied=False)

    @classmethod
    def end_lease_and_free_unit(cls, lease_id, end_date=None, apply_deposit=False):
        """
        End a lease.
        - Pays outstanding invoices from deposit.
        - Converts remaining deposit to Tenant Credit (Rollover).
        """
        try:
            with transaction.atomic():
                lease = Lease.objects.select_for_update().get(pk=lease_id)
                today = timezone.now().date()
                target_date = end_date or today
                
                lease.end_date = target_date
                lease.save(update_fields=['end_date'])
                
                deposit_message = ""
                if apply_deposit:
                    deposit = Deposit.objects.filter(lease=lease).first()
                    if deposit and deposit.amount_held > 0:
                        unpaid_invoices = Invoice.objects.filter(tenant=lease.tenant, is_paid=False).order_by('billing_period_start')
                        total_applied = Decimal('0.00')
                        remaining_deposit = deposit.amount_held

                        for inv in unpaid_invoices:
                            if remaining_deposit <= 0: break
                            inv_balance = inv.balance
                            if inv_balance <= 0: continue

                            to_pay = min(remaining_deposit, inv_balance)
                            
                            Payment.objects.create(
                                tenant=lease.tenant,
                                invoice=inv,
                                amount=to_pay,
                                method="Deposit Application",
                                reference=f"Allocation from Deposit - Lease {lease.id}",
                                payment_type='RENT' 
                            )
                            
                            LedgerEntry.objects.create(
                                lease=lease, tenant=lease.tenant, deposit=deposit, invoice=inv,
                                debit=to_pay, credit=Decimal('0.00'), entry_type=LedgerEntry.DEPOSIT,
                                description=f"Deposit applied to Invoice #{inv.id}"
                            )

                            remaining_deposit -= to_pay
                            total_applied += to_pay
                        
                        if remaining_deposit > 0:
                            Payment.objects.create(
                                tenant=lease.tenant,
                                invoice=None, 
                                amount=remaining_deposit,
                                method="Deposit Rollover",
                                reference=f"Allocation from Deposit (Refund) - Lease {lease.id}",
                                payment_type='CREDIT'
                            )
                            
                            LedgerEntry.objects.create(
                                lease=lease, tenant=lease.tenant, deposit=deposit,
                                debit=remaining_deposit, credit=Decimal('0.00'), entry_type=LedgerEntry.DEPOSIT,
                                description="Deposit balance transferred to Tenant Credit"
                            )
                            deposit_message += f" {remaining_deposit} moved to Tenant Credit."

                        deposit.amount_held = Decimal('0.00') 
                        deposit.refunded_amount += remaining_deposit 
                        deposit.notes = f"{deposit.notes or ''} [Closed: {total_applied} applied, {remaining_deposit} rolled over]"
                        deposit.save()
                        
                        if total_applied > 0:
                            deposit_message = f" Applied {total_applied} to balances.{deposit_message}"

                if target_date <= today:
                    lease.end_lease() 
                    if lease.unit:
                        lease.unit.is_occupied = False
                        lease.unit.save(update_fields=['is_occupied'])
                    status_msg = "ended immediately"
                else:
                    status_msg = f"scheduled to end on {target_date}"
                
                TenantBalance.recalc_for_tenant(lease.tenant)
                    
                return {
                    "success": True, 
                    "message": f"Lease {status_msg}.{deposit_message}"
                }

        except Lease.DoesNotExist:
            return {"success": False, "message": "Lease not found"}