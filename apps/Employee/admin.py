from django.contrib import admin
from .models import (
    EmployeeProfile, EmployeeSalary,
    AllowanceTemplate, DeductionTemplate,
    Payroll, AllowanceSnapshot, DeductionSnapshot
)


@admin.register(EmployeeSalary)
class EmployeeSalaryAdmin(admin.ModelAdmin):
    list_display = ('amount', 'effective_from', 'effective_to')


#
# Inlines for EmployeeProfil
#
class EmployeeSalaryInline(admin.TabularInline):
    model = EmployeeSalary
    extra = 1
    fields = ('amount', 'effective_from', 'effective_to')
    ordering = ('-effective_from',)


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
    list_display = ('user_full_name', 'department', 'role', 'phone_number')
    search_fields = ('user__first_name', 'user__last_name', 'department')
    list_filter = ('department', 'role')
    inlines = [EmployeeSalaryInline, AllowanceTemplateInline, DeductionTemplateInline]

    def user_full_name(self, obj):
        return obj.user.get_full_name()
    user_full_name.short_description = 'Name'


#
# Inlines for Payroll
#
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
        'month', 'gross_salary', 'total_allowances', 'total_deductions', 'net_salary',
        'is_paid', 'paid_on'
    )
    list_filter = ('is_paid', 'month')
    date_hierarchy = 'month'
    search_fields = ('employee__user__first_name', 'employee__user__last_name')
    inlines = [AllowanceSnapshotInline, DeductionSnapshotInline]

    def employee_full_name(self, obj):
        return obj.employee.user.get_full_name()
    employee_full_name.short_description = 'Employee'


#
# Separate admin for templates (optional)
#
@admin.register(AllowanceTemplate)
class AllowanceTemplateAdmin(admin.ModelAdmin):
    list_display = ('employee', 'name', 'amount', 'recurring', 'start_date', 'end_date')
    list_filter = ('recurring',)
    search_fields = ('name', 'employee__user__first_name', 'employee__user__last_name')


@admin.register(DeductionTemplate)
class DeductionTemplateAdmin(admin.ModelAdmin):
    list_display = ('employee', 'name', 'amount', 'recurring', 'start_date', 'end_date')
    list_filter = ('recurring',)
    search_fields = ('name', 'employee__user__first_name', 'employee__user__last_name')
