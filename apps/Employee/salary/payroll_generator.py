# payroll/services/payroll_generator.py
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.db.models import Q, Max, Prefetch

from apps.Employee.models import (
    EmployeeProfile, Payroll, EmployeeSalary,
    AllowanceTemplate, DeductionTemplate,
    AllowanceSnapshot, DeductionSnapshot,
)

logger = logging.getLogger(__name__)


def _get_new_month(run_date):
    return run_date.replace(day=1)

from django.db.models import Max
from django.db import transaction


def generate_payroll_for_employee(emp, new_month):
    """
    Try to generate exactly one payroll for this employee and month.
    Returns the created Payroll or None.
    """
    # 1) skip if there's already a payroll for new_month
    if emp.payrolls.filter(month=new_month).exists():
        return None

    # 2) find the last payroll month
    agg = emp.payrolls.aggregate(last_month=Max('month'))
    last_month = agg['last_month']
    if last_month:
        last_payroll = emp.payrolls.get(month=last_month)
        # skip if that payroll isn’t yet paid
        if not last_payroll.is_paid:
            logger.info(f"Skipping emp {emp.pk}: last payroll for {last_month} still unpaid")
            return None

    # 3) fetch active salary snapshot
    salary = emp.salaries.filter(effective_to__isnull=True) \
                         .order_by('-effective_from') \
                         .first()
    if not salary:
        logger.warning(f"No active salary for emp {emp.pk}, skipping")
        return None

    gross = salary.amount

    # 4) gather templates
    allow_qs = AllowanceTemplate.objects.filter(
        employee=emp,
        start_date__lte=new_month,
    ).filter(Q(end_date__isnull=True) | Q(end_date__gte=new_month))

    deduct_qs = DeductionTemplate.objects.filter(
        employee=emp,
        start_date__lte=new_month,
    ).filter(Q(end_date__isnull=True) | Q(end_date__gte=new_month))

    total_allow  = sum(a.amount for a in allow_qs)
    total_deduct = sum(d.amount for d in deduct_qs)
    net = gross + total_allow - total_deduct

    # 5) create payroll + snapshots
    with transaction.atomic():
        payroll = Payroll.objects.create(
            employee=emp,
            salary_snapshot=salary,
            month=new_month,
            gross_salary=gross,
            total_allowances=total_allow,
            total_deductions=total_deduct,
            net_salary=net,
        )
        # bulk snapshot creation
        AllowanceSnapshot.objects.bulk_create([
            AllowanceSnapshot(
                payroll=payroll,
                template=tmpl,
                name=tmpl.name,
                amount=tmpl.amount,
                recurring=tmpl.recurring,
            ) for tmpl in allow_qs
        ])
        DeductionSnapshot.objects.bulk_create([
            DeductionSnapshot(
                payroll=payroll,
                template=tmpl,
                name=tmpl.name,
                amount=tmpl.amount,
                recurring=tmpl.recurring,
            ) for tmpl in deduct_qs
        ])
        logger.info(f"Created payroll {payroll.pk} for emp {emp.pk} month {new_month}")

    return payroll



def generate_monthly_payroll(run_date=None):
    """
    Loop all employees and generate payroll for next month if possible.
    Returns count of payrolls created.
    """
    if run_date is None:
        run_date = timezone.now().date()
    new_month = _get_new_month(run_date)

    # if *any* unpaid payroll exists, do nothing
    if Payroll.objects.filter(is_paid=False).exists():
        logger.warning("Skipped generation: unpaid payrolls exist.")
        return 0

    # prefetch related so each emp.fetch hits minimal queries
    qs = EmployeeProfile.objects.all().prefetch_related(
        Prefetch('salaries', queryset=EmployeeSalary.objects.filter(effective_to__isnull=True)),
        Prefetch('allowance_templates'),
        Prefetch('deduction_templates'),
        Prefetch('payrolls', queryset=Payroll.objects.only('month', 'is_paid'))
    )

    created = 0
    for emp in qs:
        if generate_payroll_for_employee(emp, new_month):
            created += 1

    logger.info(f"Total payrolls generated: {created}")
    return created
