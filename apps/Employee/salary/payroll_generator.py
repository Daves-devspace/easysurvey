
from apps.Employee.models import (
    EmployeeProfile, Payroll, EmployeeSalary,
    AllowanceSnapshot, DeductionSnapshot,
)

import logging
from datetime import date
from django.db import transaction
from django.db.models import Max, Q, Prefetch
from django.utils import timezone

logger = logging.getLogger(__name__)

def _get_new_month(run_date: date):
    """Normalize any date to the first of its month."""
    return run_date.replace(day=1)

def generate_payroll_for_employee(emp, new_month):
    """
    Try to generate exactly one payroll for this employee & month.
    Returns the created Payroll or None.
    """
    # 1) Already exists?
    if emp.payrolls.filter(month=new_month).exists():
        logger.debug(f"[PayrollGen] skip {emp.pk}: payroll exists for {new_month}")
        return None

    # 2) Last payroll must be paid
    agg = emp.payrolls.aggregate(last_month=Max('month'))
    last_month = agg['last_month']
    if last_month:
        last_pay = emp.payrolls.get(month=last_month)
        if not last_pay.is_paid:
            logger.info(f"[PayrollGen] skip {emp.pk}: last payroll {last_month} unpaid")
            return None

    # 3) Pick the open salary snapshot
    salary = (
        emp.salaries
           .filter(effective_to__isnull=True)
           .order_by('-effective_from')
           .first()
    )
    if not salary:
        logger.warning(f"[PayrollGen] skip {emp.pk}: no active salary snapshot")
        return None

    gross = salary.amount

    # 4) Gather templates for this month
    allow_qs = emp.allowance_templates.filter(
        start_date__lte=new_month
    ).filter(Q(end_date__isnull=True) | Q(end_date__gte=new_month))

    deduct_qs = emp.deduction_templates.filter(
        start_date__lte=new_month
    ).filter(Q(end_date__isnull=True) | Q(end_date__gte=new_month))

    total_allow = sum(a.amount for a in allow_qs)
    total_deduct = sum(d.amount for d in deduct_qs)
    net = gross + total_allow - total_deduct

    # 5) Create Payroll + Snapshots
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

        # bulk-create allowance snapshots
        AllowanceSnapshot.objects.bulk_create([
            AllowanceSnapshot(
                payroll=payroll,
                template=tmpl,
                name=tmpl.name,
                amount=tmpl.amount,
                recurring=tmpl.recurring,
                start_date=tmpl.start_date,
                end_date=tmpl.end_date,
            ) for tmpl in allow_qs
        ])
        # bulk-create deduction snapshots
        DeductionSnapshot.objects.bulk_create([
            DeductionSnapshot(
                payroll=payroll,
                template=tmpl,
                name=tmpl.name,
                amount=tmpl.amount,
                recurring=tmpl.recurring,
                start_date=tmpl.start_date,
                end_date=tmpl.end_date,
            ) for tmpl in deduct_qs
        ])

        logger.info(f"[PayrollGen] created payroll {payroll.pk} for emp {emp.pk} month {new_month}")
    return payroll


def generate_monthly_payroll(run_date=None):
    """
    Loop through all employees and generate payroll for the next month if possible.
    Returns the count of newly created Payrolls.
    """
    if run_date is None:
        run_date = timezone.now().date()
    new_month = _get_new_month(run_date)
    logger.info(f"[PayrollGenAll] start generation for {new_month}")

    # If *any* unpaid payroll exists, we abort globally.
    if Payroll.objects.filter(is_paid=False).exists():
        logger.warning("[PayrollGenAll] abort: unpaid payrolls exist")
        return 0

    # Prefetch related data to minimize queries
    qs = EmployeeProfile.objects.all().prefetch_related(
        Prefetch('salaries', queryset=EmployeeSalary.objects.filter(effective_to__isnull=True)),
        'allowance_templates',
        'deduction_templates',
        Prefetch('payrolls', queryset=Payroll.objects.only('month', 'is_paid'))
    )

    created = 0
    for emp in qs:
        if generate_payroll_for_employee(emp, new_month):
            created += 1

    logger.info(f"[PayrollGenAll] total new payrolls generated: {created}")
    return created
