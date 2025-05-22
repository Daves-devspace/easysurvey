# signals.py
from datetime import timedelta, date

from django.contrib.auth import get_user_model
from django.db import models
from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver
from django.contrib.auth.models import Group
from django.utils.timezone import now

from .models import EmployeeProfile, EmployeeSalary, AllowanceSnapshot, DeductionSnapshot, Payroll, AllowanceTemplate, \
    DeductionTemplate
from apps.Employee.salary.payroll_generator import  generate_payroll_for_employee

import logging

logger = logging.getLogger(__name__)




User = get_user_model()

@receiver(post_save, sender=User)
def create_admin_profile(sender, instance, created, **kwargs):
    if instance.is_superuser:
        profile, created = EmployeeProfile.objects.get_or_create(
            user=instance,
            defaults={'role': EmployeeProfile.RoleChoices.ADMIN}
        )

        if not created and profile.role != EmployeeProfile.RoleChoices.ADMIN:
            profile.Role = EmployeeProfile.RoleChoices.ADMIN
            profile.save()



@receiver(pre_save, sender=EmployeeProfile)
def save_old_role(sender, instance, **kwargs):
    if instance.pk:
        try:
            instance._old_role = sender.objects.get(pk=instance.pk).role
        except sender.DoesNotExist:
            instance._old_role = None

@receiver(post_save, sender=EmployeeProfile)
def assign_group_based_on_role(sender, instance, **kwargs):
    new_role = instance.role
    user = instance.user

    if new_role and user:
        group, _ = Group.objects.get_or_create(name=new_role)
        user.groups.clear()
        user.groups.add(group)





@receiver(post_save, sender=EmployeeSalary)
def auto_generate_payroll_on_salary_create(sender, instance, created, **kwargs):
    """
    As soon as a new salary snapshot goes in, kick off payroll for that month
    (or next month if you prefer).  It will respect your “skip if unpaid” logic.
    """
    if not created:
        return

    emp       = instance.employee
    # we’ll create for the salary’s own effective_from month:
    new_month = instance.effective_from.replace(day=1)

    # only generate if there is no payroll yet and no unpaid backlog
    # (generate_payroll_for_employee already checks both conditions)
    generate_payroll_for_employee(emp, new_month)


@receiver(post_save, sender=EmployeeSalary)
def sync_updated_salary_to_existing_payroll(sender, instance, created, **kwargs):
    """
    If you later update an EmployeeSalary (e.g. correct amount),
    keep any *existing* payroll that was based on it in sync.
    """
    # find any payrolls already pointing at this snapshot
    snaps = Payroll.objects.filter(salary_snapshot=instance)
    for p in snaps:
        p.gross_salary = instance.amount
        # re‑run your totals logic (snapshots and net salary)
        p.total_allowances  = sum(a.amount for a in p.allowance_snapshots.all())
        p.total_deductions  = sum(d.amount for d in p.deduction_snapshots.all())
        p.net_salary        = p.gross_salary + p.total_allowances - p.total_deductions
        p.save()



# apps/Employee/signals.py
def _recalc_payroll(p: Payroll):
    p.total_allowances  = p.allowance_snapshots.aggregate(sum=models.Sum('amount'))['sum'] or 0
    p.total_deductions  = p.deduction_snapshots.aggregate(sum=models.Sum('amount'))['sum'] or 0
    p.net_salary        = p.gross_salary + p.total_allowances - p.total_deductions
    p.save(update_fields=['total_allowances','total_deductions','net_salary'])




def get_current_payroll(employee):
    today = now().date().replace(day=1)
    try:
        return Payroll.objects.get(employee=employee, month=today)
    except Payroll.DoesNotExist:
        return None

@receiver(post_save, sender=AllowanceTemplate)
@receiver(post_save, sender=DeductionTemplate)
def template_post_save(sender, instance, **kwargs):
    """
    On template create/update, update or create the snapshot
    for the current payroll, copying name, amount, recurring,
    plus the start_date and end_date from the template.
    """
    payroll = get_current_payroll(instance.employee)
    if not payroll:
        return

    defaults = {
        'name':       instance.name,
        'amount':     instance.amount,
        'recurring':  instance.recurring,
        'start_date': instance.start_date,
        'end_date':   instance.end_date,
    }

    if isinstance(instance, AllowanceTemplate):
        AllowanceSnapshot.objects.update_or_create(
            payroll=payroll,
            template=instance,
            defaults=defaults
        )
    else:
        DeductionSnapshot.objects.update_or_create(
            payroll=payroll,
            template=instance,
            defaults=defaults
        )

    _recalc_payroll(payroll)


@receiver(post_delete, sender=AllowanceTemplate)
@receiver(post_delete, sender=DeductionTemplate)
def template_post_delete(sender, instance, **kwargs):
    """
    On template delete, Django’s CASCADE will drop the snapshot,
    but we still need to recalc the current payroll.
    """
    payroll = get_current_payroll(instance.employee)
    if payroll:
        _recalc_payroll(payroll)




# @receiver(post_save, sender=AllowanceTemplate)
# @receiver(post_delete, sender=AllowanceTemplate)
# def sync_allowance_snapshot(sender, instance, **kwargs):
#     """
#     On create/update/delete of a template, patch the current payroll snapshot
#     and then recalc that payroll’s totals.
#     """
#     # find *this* employee’s payroll for this month
#     today = now().date().replace(day=1)
#     try:
#         payroll = Payroll.objects.get(employee=instance.employee, month=today)
#     except Payroll.DoesNotExist:
#         return
#
#     # on delete: purge
#     if kwargs.get('signal') == post_delete:
#         AllowanceSnapshot.objects.filter(payroll=payroll, template=instance).delete()
#     else:
#         # create or update
#         AllowanceSnapshot.objects.update_or_create(
#             payroll=payroll,
#             template=instance,
#             defaults={
#                 'name':      instance.name,
#                 'amount':    instance.amount,
#                 'recurring': instance.recurring,
#             }
#         )
#
#     _recalc_payroll(payroll)
#
#
# @receiver(post_save, sender=DeductionTemplate)
# @receiver(post_delete, sender=DeductionTemplate)
# def sync_deduction_snapshot(sender, instance, **kwargs):
#     """
#     Same as above but for deductions.
#     """
#     today = now().date().replace(day=1)
#     try:
#         payroll = Payroll.objects.get(employee=instance.employee, month=today)
#     except Payroll.DoesNotExist:
#         return
#
#     if kwargs.get('signal') == post_delete:
#         DeductionSnapshot.objects.filter(payroll=payroll, template=instance).delete()
#     else:
#         DeductionSnapshot.objects.update_or_create(
#             payroll=payroll,
#             template=instance,
#             defaults={
#                 'name':      instance.name,
#                 'amount':    instance.amount,
#                 'recurring': instance.recurring,
#             }
#         )
#
#     _recalc_payroll(payroll)