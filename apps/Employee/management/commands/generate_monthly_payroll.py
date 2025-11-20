
# payroll/management/commands/generate_monthly_payroll.py
from django.core.management.base import BaseCommand

from apps.Employee.salary.payroll_generator import generate_monthly_payroll


class Command(BaseCommand):
    help = "Generate payroll for next month if all previous payrolls are paid."

    def handle(self, *args, **options):
        count = generate_monthly_payroll()
        if count:
            self.stdout.write(self.style.SUCCESS(f"✅ Generated {count} new payroll(s)."))
        else:
            self.stdout.write(self.style.WARNING("⏭ No payrolls generated."))
