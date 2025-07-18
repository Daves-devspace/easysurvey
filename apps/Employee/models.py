from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta


# --------------------------
# Employee Profile
# --------------------------
class EmployeeProfile(models.Model):
    class RoleChoices(models.TextChoices):
        SURVEYOR = 'Surveyor', 'Surveyor'
        FRONTOFFICE = 'FrontOffice', 'Front Office'
        ADMIN = 'Admin', 'Admin'

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    department = models.CharField(max_length=100, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    profile_picture = models.ImageField(upload_to='profiles/', blank=True, null=True)
    role = models.CharField(
        max_length=20,
        choices=RoleChoices.choices,
        default=RoleChoices.FRONTOFFICE
    )

    def latest_payroll(self):
        payrolls = getattr(self, 'latest_payrolls', None)
        if payrolls:
            return payrolls[0]
        return self.payrolls.order_by('-month').first()

    def latest_net_salary(self):
        payroll = self.latest_payroll()
        return payroll.net_salary if payroll else 0

    def latest_total_allowances(self):
        payroll = self.latest_payroll()
        return sum(a.amount for a in payroll.allowance_snapshots.all()) if payroll else 0

    def latest_total_deductions(self):
        payroll = self.latest_payroll()
        return sum(d.amount for d in payroll.deduction_snapshots.all()) if payroll else 0

    def __str__(self):
        return f"{self.user.first_name} {self.user.last_name}"


# --------------------------
# Employee Salary Template
# --------------------------
class EmployeeSalary(models.Model):
    employee = models.ForeignKey(EmployeeProfile, on_delete=models.CASCADE, related_name='salaries')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['-effective_from']
        unique_together = ('employee', 'effective_from')

    def __str__(self):
        return f"{self.employee}: {self.amount} from {self.effective_from}"


# --------------------------
# Allowance & Deduction Templates
# --------------------------
class AllowanceTemplate(models.Model):
    employee = models.ForeignKey(EmployeeProfile, on_delete=models.CASCADE, related_name='allowance_templates')
    name = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    recurring = models.BooleanField(default=True)
    start_date = models.DateField(default=timezone.now)
    end_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"{self.employee}: {self.name} {self.amount}"

class DeductionTemplate(models.Model):
    employee = models.ForeignKey(EmployeeProfile, on_delete=models.CASCADE, related_name='deduction_templates')
    name = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    recurring = models.BooleanField(default=True)
    start_date = models.DateField(default=timezone.now)
    end_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"{self.employee}: {self.name} {self.amount}"


# --------------------------
# Payroll Snapshot
# --------------------------
class Payroll(models.Model):
    employee = models.ForeignKey(EmployeeProfile, on_delete=models.CASCADE, related_name='payrolls')
    salary_snapshot = models.ForeignKey(EmployeeSalary, on_delete=models.PROTECT, related_name='payrolls')
    month = models.DateField(help_text="First day of payroll month")
    created_at = models.DateTimeField(auto_now_add=True)

    gross_salary = models.DecimalField(null=True, blank=True,max_digits=10, decimal_places=2)
    total_allowances = models.DecimalField(max_digits=10, decimal_places=2)
    total_deductions = models.DecimalField(max_digits=10, decimal_places=2)
    net_salary = models.DecimalField(max_digits=10, decimal_places=2)

    is_paid = models.BooleanField(default=False)
    paid_on = models.DateTimeField(null=True, blank=True)
    payment_reference = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        unique_together = ('employee', 'month')
        ordering = ['-month']
        indexes = [models.Index(fields=['month']), models.Index(fields=['is_paid'])]

    def __str__(self):
        return f"{self.employee.user.get_full_name()} - {self.month.strftime('%B %Y')}"


# --------------------------
# Allowance & Deduction Snapshots
# --------------------------
class AllowanceSnapshot(models.Model):
    payroll = models.ForeignKey(Payroll, on_delete=models.CASCADE, related_name='allowance_snapshots')
    template = models.ForeignKey(AllowanceTemplate, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    recurring = models.BooleanField(default=False)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"{self.name} ({self.amount}) for {self.payroll}"

class DeductionSnapshot(models.Model):
    payroll = models.ForeignKey(Payroll, on_delete=models.CASCADE, related_name='deduction_snapshots')
    template = models.ForeignKey(DeductionTemplate, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    recurring = models.BooleanField(default=False)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"{self.name} ({self.amount}) for {self.payroll}"
