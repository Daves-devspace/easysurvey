Tenant Management System Documentation
1. Core Concepts

The system models a property management workflow with tenants, leases, utilities (water), deposits, and invoices.

WaterCompany: Represents the water provider for a property.

Property: A building or complex. Has water billing policy (shared, metered, prepaid) and a billing day.

Unit: Individual rentable units within a property.

Tenant: A person renting a unit.

Lease: Connects a tenant to a unit, tracks deposit, and active/inactive status.

Deposit: Tenant’s security deposit tied to a lease, can be applied to invoices or refunded.

MeterReading: Tracks water consumption per unit; usage and amount are auto-calculated.

Invoice & InvoiceLine: Rent and utility bills for tenants. Lines represent charges (rent, water, deposit applied, etc.).

Payment: Records actual payments made for invoices.

TenantBalance: Tracks unallocated credit/debits for a tenant.

LedgerEntry: Detailed financial entries (credits/debits) for transparency.

Receipt: Links a payment to a receipt number.

NotificationLog: Tracks messages sent to tenants (SMS, Email, WhatsApp).

2. Key Workflows
2.1 Lease & Unit Occupancy

Creating a lease:

Marks the associated unit as is_occupied=True.

Creates a Deposit record for the lease if deposit_amount > 0.

Ending a lease:

Marks unit as is_occupied=False.

Deposit becomes refundable (requires manual admin trigger).

2.2 Invoice Management

InvoiceLine signals (post_save & post_delete):

Automatically recalc Invoice.total_amount whenever a line is added, updated, or deleted.

Invoice status logic (Invoice.update_status_for_lease):

DRAFT → No rent/water lines.

PENDING → Rent exists, water missing.

FINALIZED → Rent and water exist.

Auto-apply tenant credit:

When a new invoice is created, the system applies any existing TenantBalance credit to that invoice.

2.3 Meter Reading

Pre-save:

Calculates usage as current_reading - previous_reading.

Amount is computed based on usage × water_rate.

Post-save:

Enqueues an async task (process_new_meter_reading) for generating water invoice lines.

Post-delete:

Removes water invoice lines if reading is deleted and no other readings exist in that billing period.

2.4 Payments

Payment signal:

Updates balance_after.

Creates a Receipt automatically.

Marks invoice as paid if fully covered.

_apply_credit_and_deposit:

Core function for applying payments or unallocated tenant credits.

Logic:

Uses real payment if provided, otherwise unallocated tenant credits.

Applies to unpaid invoices first (oldest first).

Optionally top-ups deposit (if apply_to_deposit=True).

Stores any overpayment as tenant credit (LedgerEntry).

Recalculates TenantBalance for accurate account standing.

apply_payment_safe:

Public API to apply external cash payments safely using transactions.

2.5 Deposit Lifecycle

Deposit is created automatically when a lease is created.

Can be applied to invoices via _apply_credit_and_deposit or manually.

Refund is only triggered manually by admin when lease ends.

Ledger entries maintain transparency for deposits applied or refunded.

3. Tenant Balance & Ledger

TenantBalance.recalc_for_tenant:

Calculates:

TenantBalance = Sum of unpaid invoice balances + Total debits - Total unallocated credits


Ensures balance is accurate and avoids double-counting credits.

LedgerEntry:

Records every debit/credit transaction.

Includes deposits applied, rent payments, overpayments, and other adjustments.

4. Signals Overview
Signal	Purpose
post_save Lease	Mark unit occupied, create deposit
pre_save Lease	Mark unit vacant on lease end, handle deposit eligibility
post_save InvoiceLine	Recalc invoice total
post_delete InvoiceLine	Recalc invoice total
post_save Payment	Update invoice balance, create receipt, mark invoice paid
pre_save MeterReading	Calculate usage & amount
post_save MeterReading	Enqueue async task for invoice generation
post_delete MeterReading	Remove associated water invoice lines
post_save Invoice	Auto-apply tenant credit to new invoice
5. Utilities

Quantize function:

Ensures monetary values are consistently stored at 2 decimal places.

month_bounds_for(date):

Returns first and last day of a month for billing or report generation.

get_applicable_rate_for_date:

Fetches correct water rate for a property on a specific date.

6. System Notes

Signals maintain consistency automatically (e.g., balances, invoice totals, unit occupancy).

All financial changes flow through LedgerEntry for auditing.

Tenant credits and deposits are handled separately but integrated into invoice payment logic.

Async tasks are used for resource-intensive operations like generating invoices from meter readings.

<img width="1536" height="1024" alt="image" src="https://github.com/user-attachments/assets/2fde92c5-04f5-4602-8121-886b0ba4cf8d" />

