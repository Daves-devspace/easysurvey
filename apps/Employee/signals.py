# signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import Group
from .models import EmployeeProfile, EmployeeSalary
from apps.Employee.salary.payroll_generator import generate_monthly_payroll

import logging

logger = logging.getLogger(__name__)



@receiver(post_save, sender=EmployeeProfile)
def assign_group_based_on_role(sender, instance, created, **kwargs):
    if created:
        role = instance.role
        user = instance.user
        if role:
            group, _ = Group.objects.get_or_create(name=role)
            user.groups.add(group)






# payroll/signals.py


@receiver(post_save, sender=EmployeeSalary)
def auto_generate_payroll_on_salary_change(sender, instance, **kwargs):
    """
    After any salary is created or updated, attempt to bulk-generate
    this month’s payrolls (skipping those already done or unpaid).
    """
    # run for *all* employees (including this one)
    # you might pass timezone.now().date() if you need control
    count = generate_monthly_payroll()

    # optional: log how many new payrolls were spun up
    if count:
        logger.info(f"[signal] auto-generated {count} payroll(s) after salary change")
    else:
        logger.info("[signal] no payrolls generated (either unpaid exist or none needed)")
