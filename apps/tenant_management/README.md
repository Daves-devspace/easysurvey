Billing Workflow & Scenario Documentation

1. Core Philosophy: "Rent Forward, Water Back"

To ensure tenants receive a single, consolidated bill, this system uses a rolling cycle approach:

Rent is charged in Advance (Prepaid).

Example: The bill received in February charges for February's Rent.

Water is charged in Arrears (Postpaid).

Example: The bill received in February charges for January's Water usage.

2. Configuration Definitions

Rent Roll Date (Day 1): The system always generates the draft invoice for rent on the 1st of the month.

Billing Day (e.g., Day 5): The day the invoice is finalized, locked, and sent to the tenant. This 5-day gap allows time for caretakers to input meter readings.

3. Scenario A: Existing Tenants ("Old Tenants")

Context: A tenant who has been living in the property since January.

This follows the standard automated cycle handled by the BillingCycleService.

The Timeline (February Example)

Date

System Action

Invoice State

Logic

Feb 1

generate_rent_roll runs.

PENDING

System creates Invoice #101. Adds February Rent. Checks for existing credits and applies them.

Feb 2-4

Caretaker enters reading.

PENDING

InvoiceService detects the Pending invoice. It calculates usage (Current - Jan Reading) and adds January Water line item.

Feb 5

process_billing_day runs.

FINALIZED

System checks Invoice #101. 



1. Has Rent? ✅ 



2. Has Water? ✅ 



Result: Status changes to FINALIZED. Notification sent.

Result: The tenant receives one bill on Feb 5th containing Feb Rent + Jan Water.

4. Scenario B: New Tenant (Late Move-In)

Context: A tenant moves in on February 15th, which is PAST the Rent Roll (Feb 1) and PAST the Billing Day (Feb 5).

The automated cron jobs will not pick this up because the dates have passed. Instead, the Onboarding Service handles this immediately.

The Timeline (Move-In Day)

Action: Admin creates tenant via "Add Tenant" form with Start Date: Feb 15.

Service Trigger: TenantLeaseService executes.

Immediate Invoice: The system does not wait for March 5th. It generates a Move-In Invoice immediately.

Line 1: Deposit (Full Amount).

Line 2: Rent (Prorated).

Calculation: (Monthly Rent / Days in Month) × Remaining Days (Feb 15–28).

Line 3: Water.

Logic: None. (Usage is 0.00).

Status: FINALIZED.

Result: The tenant must pay this invoice immediately to get the keys.

The First "Standard" Cycle (March)

Now that the tenant is in the system, they join the standard queue for March.

Date

System Action

Logic

Mar 1

generate_rent_roll

Creates Invoice #102 with March Rent. Status: PENDING.

Mar 1-4

Meter Reading

Caretaker enters reading. System calculates usage from Feb 15 (Move-in) to Current Reading. Adds this to Invoice #102.

Mar 5

process_billing_day

Finalizes Invoice #102.

Result: The March 5th bill contains full March Rent + Water usage for the half-month of Feb.

5. Edge Case FAQ

Q: What happens if the Caretaker forgets to enter readings by the 5th?

A: The process_billing_day task checks for readiness.

If the property is Metered and the invoice has Rent but NO Water, the system will SKIP finalization.

The invoice remains PENDING.

The system will check again the next day (Feb 6th, 7th, etc.).

As soon as the reading is entered, the invoice becomes valid and will be finalized the next morning.

Q: What happens if a tenant moves in on Feb 2nd (Before Billing Day)?

A:

Move-In Invoice (Feb 2): Generated immediately by TenantLeaseService. Contains Deposit + Prorated Rent (Feb 2-28). No Water.

Billing Day (Feb 5): The automation runs. It sees the tenant already has a generated invoice for February (the move-in one). It calculates water usage from Feb 2 to Feb 5.

Result: Usually, usage is 0 or negligible for 3 days.

Outcome: The tenant effectively skips the Feb 5th Water bill (because they just got here) and pays their first full water bill on March 5th.

Q: How are "Tenant Credits" handled?

A:

If a tenant overpaid their Move-In invoice by 500 KES, that money sits in the Payment table as unallocated.

On March 1st, when the generate_rent_roll runs, the system automatically looks for unallocated funds and applies the 500 KES to the new March Invoice immediately. The tenant will see a reduced balance due.

6. Technical Implementation References

Rent Roll (1st of Month): apps.tenant_management.services.billing_cycle_service.BillingCycleService.generate_rent_roll

Finalization (5th of Month): apps.tenant_management.services.billing_cycle_service.BillingCycleService.process_billing_day

Late Move-In Logic: apps.tenant_management.lease.services.TenantLeaseService.save_tenant_with_lease