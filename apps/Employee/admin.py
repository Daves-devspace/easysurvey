from django.contrib import admin
from django.contrib.auth import get_user_model
from .forms import UnifiedEmployeeProfileForm
from .models import (
    EmployeeProfile, EmployeeSalary,
    AllowanceTemplate, DeductionTemplate,
    Payroll, AllowanceSnapshot, DeductionSnapshot
)

User = get_user_model()


# -------------------------
# Employee Salary
# -------------------------
@admin.register(EmployeeSalary)
class EmployeeSalaryAdmin(admin.ModelAdmin):
    list_display = ('employee', 'amount', 'effective_from', 'effective_to')
    list_filter = ('effective_from', 'effective_to')
    search_fields = ('employee__user__first_name', 'employee__user__last_name')


class EmployeeSalaryInline(admin.TabularInline):
    model = EmployeeSalary
    extra = 1
    fields = ('amount', 'effective_from', 'effective_to')
    ordering = ('-effective_from',)


# -------------------------
# Allowance / Deduction Templates
# -------------------------
class AllowanceTemplateInline(admin.TabularInline):
    model = AllowanceTemplate
    extra = 1
    fields = ('name', 'amount', 'recurring', 'start_date', 'end_date')


class DeductionTemplateInline(admin.TabularInline):
    model = DeductionTemplate
    extra = 1
    fields = ('name', 'amount', 'recurring', 'start_date', 'end_date')


@admin.register(EmployeeProfile)
class EmployeeProfileAdmin(admin.ModelAdmin):
    form = UnifiedEmployeeProfileForm  # <--- unified form here
    list_display = ('user_full_name', 'department', 'role', 'phone_number', 'email')
    search_fields = ('user__first_name', 'user__last_name', 'user__email', 'department')
    list_filter = ('department', 'role')
    inlines = [EmployeeSalaryInline, AllowanceTemplateInline, DeductionTemplateInline]

    def user_full_name(self, obj):
        return obj.user.get_full_name()
    user_full_name.short_description = 'Name'

    def email(self, obj):
        return obj.user.email
    email.admin_order_field = 'user__email'
    
    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        # pass the current user to the form
        class FormWithUser(form):
            def __new__(cls, *args, **fkwargs):
                fkwargs['user'] = request.user
                return form(*args, **fkwargs)
        return FormWithUser


# -------------------------
# Payroll and Snapshots
# -------------------------
class AllowanceSnapshotInline(admin.TabularInline):
    model = AllowanceSnapshot
    extra = 0
    readonly_fields = ('template', 'name', 'amount', 'recurring')
    can_delete = False


class DeductionSnapshotInline(admin.TabularInline):
    model = DeductionSnapshot
    extra = 0
    readonly_fields = ('template', 'name', 'amount', 'recurring')
    can_delete = False


@admin.register(Payroll)
class PayrollAdmin(admin.ModelAdmin):
    list_display = (
        'employee_full_name',
        'month', 'gross_salary', 'total_allowances',
        'total_deductions', 'net_salary',
        'is_paid', 'paid_on'
    )
    list_filter = ('is_paid', 'month')
    date_hierarchy = 'month'
    search_fields = ('employee__user__first_name', 'employee__user__last_name')
    inlines = [AllowanceSnapshotInline, DeductionSnapshotInline]

    def employee_full_name(self, obj):
        return obj.employee.user.get_full_name()
    employee_full_name.short_description = 'Employee'


# -------------------------
# Separate Template Admins (Optional)
# -------------------------
@admin.register(AllowanceTemplate)
class AllowanceTemplateAdmin(admin.ModelAdmin):
    list_display = ('employee', 'name', 'amount', 'recurring', 'start_date', 'end_date')
    list_filter = ('recurring', 'start_date', 'end_date')
    search_fields = ('name', 'employee__user__first_name', 'employee__user__last_name')


@admin.register(DeductionTemplate)
class DeductionTemplateAdmin(admin.ModelAdmin):
    list_display = ('employee', 'name', 'amount', 'recurring', 'start_date', 'end_date')
    list_filter = ('recurring', 'start_date', 'end_date')
    search_fields = ('name', 'employee__user__first_name', 'employee__user__last_name')
