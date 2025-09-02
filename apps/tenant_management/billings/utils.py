# leases/utils.py (or billing/utils.py depending on your structure)

from django.utils import timezone
from decimal import Decimal
from apps.tenant_management.models import Lease, Invoice, MeterReading, WaterRate

def generate_monthly_invoices():
    """
    Generates monthly invoices for all active leases.

    ✅ Handles water billing correctly:
        - Uses the correct WaterRate based on the invoice month.
        - If water rates changed in a specific month, only invoices 
          generated from that month onward use the new rate.
        - Past invoices remain calculated with the historical rate.

    ✅ Steps:
        1. Loop through active leases.
        2. Check if invoice for current month already exists → skip to avoid duplicates.
        3. Get meter readings for this lease for the current month.
        4. Fetch the correct water rate based on reading/invoice date.
        5. Calculate usage = (current - previous) * rate.
        6. Create invoice with water charge included.
    """

    today = timezone.now()
    current_month = today.month
    current_year = today.year

    # Fetch all active leases
    leases = Lease.objects.filter(is_active=True)

    for lease in leases:
        # Skip if invoice already exists for this lease and month
        if Invoice.objects.filter(
            lease=lease, month=current_month, year=current_year
        ).exists():
            continue

        # Get latest meter reading for this lease
        reading = (
            MeterReading.objects.filter(lease=lease, reading_date__year=current_year, reading_date__month=current_month)
            .order_by("-reading_date")
            .first()
        )

        water_charge = Decimal("0.00")

        if reading and reading.previous_reading is not None:
            # Calculate usage (in cubic meters)
            usage_units = Decimal(reading.current_reading) - Decimal(reading.previous_reading)

            # ✅ Get applicable water rate for that month
            water_rate = (
                WaterRate.objects.filter(
                    effective_from__lte=reading.reading_date  # Only consider rates effective at/before reading date
                )
                .order_by("-effective_from")
                .first()
            )

            if water_rate:
                water_charge = usage_units * Decimal(water_rate.rate_per_unit)

        # Base rent + water charges
        total_amount = lease.monthly_rent + water_charge

        # Create invoice
        Invoice.objects.create(
            lease=lease,
            month=current_month,
            year=current_year,
            rent_amount=lease.monthly_rent,
            water_amount=water_charge,
            total_amount=total_amount,
            due_date=today.replace(day=5)  # Example: due by 5th of the month
        )
