from django.urls import reverse_lazy
from django.views.generic import CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.views.generic import CreateView, UpdateView, DeleteView, View
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from django.db import transaction, models
from django.utils import timezone
from datetime import timedelta

from django.urls import reverse_lazy
from django.views.generic import CreateView, UpdateView, DeleteView
from django.shortcuts import get_object_or_404, redirect
from apps.Employee.models import EmployeeProfile, AllowanceTemplate, DeductionTemplate
from apps.Employee.forms import AllowanceTemplateForm, DeductionTemplateForm

from apps.Employee.forms import EmployeeSalaryForm
import logging

from apps.Employee.models import EmployeeProfile, Payroll, AllowanceTemplate, DeductionTemplate, AllowanceSnapshot, DeductionSnapshot, EmployeeSalary
from apps.Employee.salary.payroll_generator import _get_new_month, generate_payroll_for_employee

logger = logging.getLogger(__name__)


class EmployeeSalaryCreateView(CreateView):
    model = EmployeeSalary
    form_class = EmployeeSalaryForm
    template_name = 'Employees/partials/_salary_form.html'  # adjust path as needed
    success_url = reverse_lazy('employee_list')

    def dispatch(self, request, *args, **kwargs):
        # Ensure the employee exists for this salary
        self.employee = get_object_or_404(EmployeeProfile, pk=kwargs['employee_pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **ctx):
        ctx = super().get_context_data(**ctx)
        ctx['employee'] = self.employee
        ctx['salary_action_url'] = reverse_lazy('salary_create', args=[self.employee.pk])
        return ctx

    def form_valid(self, form):
        # Prevent overlapping salary periods
        form.instance.employee = self.employee
        # If there's an existing active salary, close it
        active = EmployeeSalary.objects.filter(employee=self.employee, effective_to__isnull=True).first()
        if active:
            active.effective_to = form.instance.effective_from - timedelta(days=1)
            active.save(update_fields=['effective_to'])

        try:
            with transaction.atomic():
                resp = super().form_valid(form)
                messages.success(self.request, "✅ Salary added successfully.")
                return resp
        except Exception as e:
            messages.error(self.request, f"❌ Failed to add salary: {e}")
            return redirect(self.success_url)

    def form_invalid(self, form):
        messages.error(self.request, "❌ Please fix the errors in the salary form.")
        return self.render_to_response(self.get_context_data(form=form))


class EmployeeSalaryUpdateView(UpdateView):
    model = EmployeeSalary
    form_class = EmployeeSalaryForm
    template_name = 'Employees/partials/_salary_form.html'
    success_url = reverse_lazy('employee_list')

    def get_context_data(self, **ctx):
        ctx = super().get_context_data(**ctx)
        salary = self.get_object()
        ctx['employee'] = salary.employee
        ctx['salary_action_url'] = reverse_lazy('salary_update', args=[salary.pk])
        return ctx

    def form_valid(self, form):
        try:
            resp = super().form_valid(form)
            messages.success(self.request, "✅ Salary updated successfully.")
            return resp
        except Exception as e:
            messages.error(self.request, f"❌ Failed to update salary: {e}")
            return self.form_invalid(form)

    def form_invalid(self, form):
        messages.error(self.request, "❌ Please fix the errors in the salary form.")
        return self.render_to_response(self.get_context_data(form=form))


class EmployeeSalaryDeleteView(DeleteView):
    model = EmployeeSalary
    template_name = 'Employees/partials/_salary_confirm_delete.html'
    success_url = reverse_lazy('employee_list')

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        try:
            self.object.delete()
            messages.success(request, "✅ Salary record deleted.")
        except Exception as e:
            messages.error(request, f"❌ Could not delete salary: {e}")
        return redirect(self.success_url)

# urls.py snippet


# ALLOWANCE CBVs

class AllowanceCreateView(CreateView):
    model = AllowanceTemplate
    form_class = AllowanceTemplateForm
    template_name = 'Employees/partials/allowance_form.html'

    def form_valid(self, form):
        form.instance.employee = get_object_or_404(EmployeeProfile, pk=self.kwargs['employee_id'])
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('employee_detail', kwargs={'pk': self.kwargs['employee_id']})


class AllowanceUpdateView(UpdateView):
    model = AllowanceTemplate
    form_class = AllowanceTemplateForm
    template_name = 'Employees/partials/allowance_form.html'

    def get_success_url(self):
        return reverse_lazy('employee_detail', kwargs={'pk': self.object.employee.id})


class AllowanceDeleteView(DeleteView):
    model = AllowanceTemplate
    template_name = 'Employees/partials/allowance_confirm_delete.html'

    def get_success_url(self):
        return reverse_lazy('employee_detail', kwargs={'pk': self.object.employee.id})


# DEDUCTION CBVs

class DeductionCreateView(CreateView):
    model = DeductionTemplate
    form_class = DeductionTemplateForm
    template_name = 'Employees/partials/deduction_form.html'

    def form_valid(self, form):
        form.instance.employee = get_object_or_404(EmployeeProfile, pk=self.kwargs['employee_id'])
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('employee_detail', kwargs={'pk': self.kwargs['employee_id']})


class DeductionUpdateView(UpdateView):
    model = DeductionTemplate
    form_class = DeductionTemplateForm
    template_name = 'Employees/partials/deduction_form.html'

    def get_success_url(self):
        return reverse_lazy('employee_detail', kwargs={'pk': self.object.employee.id})


class DeductionDeleteView(DeleteView):
    model = DeductionTemplate
    template_name = 'Employees/partials/deduction_confirm_delete.html'

    def get_success_url(self):
        return reverse_lazy('employee_detail', kwargs={'pk': self.object.employee.id})






class EmployeePayrollGenerateView(View):
    def post(self, request, employee_pk):
        emp = get_object_or_404(EmployeeProfile, pk=employee_pk)
        new_month = _get_new_month(None)   # defaults to today’s month
        payroll = generate_payroll_for_employee(emp, new_month)
        if payroll:
            messages.success(request, f"✅ Payroll for {payroll.month:%B %Y} created.")
        else:
            messages.warning(request, "⚠️ Could not generate payroll.")
        return redirect('employee_list')

