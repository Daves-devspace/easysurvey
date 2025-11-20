from django.http import HttpResponseRedirect
from django.urls import reverse_lazy
from django.utils.timezone import now
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

# views.py


class AllowanceCreateView(CreateView):
    model = AllowanceTemplate
    form_class = AllowanceTemplateForm
    template_name = 'Employees/partials/allowance_form.html'

    def dispatch(self, request, *args, **kwargs):
        # grab employee once
        self.employee = get_object_or_404(EmployeeProfile, pk=kwargs['employee_id'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **ctx):
        ctx = super().get_context_data(**ctx)
        ctx['employee']    = self.employee
        ctx['employee_id'] = self.employee.pk
        return ctx

    def form_valid(self, form):
        form.instance.employee = self.employee
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('employee_list')


class AllowanceUpdateView(UpdateView):
    model = AllowanceTemplate
    form_class = AllowanceTemplateForm
    template_name = 'Employees/partials/allowance_form.html'

    def get_context_data(self, **ctx):
        ctx = super().get_context_data(**ctx)
        ctx['employee']    = self.object.employee
        ctx['employee_id'] = self.object.employee.pk
        return ctx

    def get_success_url(self):
        return reverse_lazy('employee_list')


class AllowanceDeleteView(DeleteView):
    model = AllowanceTemplate
    template_name = 'Employees/partials/allowance_confirm_delete.html'

    def get_context_data(self, **ctx):
        ctx = super().get_context_data(**ctx)
        ctx['employee']    = self.object.employee
        ctx['employee_id'] = self.object.employee.pk
        return ctx

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()

        used_in_paid = AllowanceSnapshot.objects.filter(
            template=self.object,
            payroll__is_paid=True
        ).exists()

        if used_in_paid:
            messages.error(
                request,
                "Cannot delete this allowance template because it has already been applied in a paid payroll."
            )
            return HttpResponseRedirect(self.get_success_url())

        response = super().delete(request, *args, **kwargs)
        messages.success(request, "Allowance template deleted successfully.")
        return response

    def get_success_url(self):
        return reverse_lazy('employee_list')



# DEDUCTION CBVs

# views.py


class DeductionCreateView(CreateView):
    model = DeductionTemplate
    form_class = DeductionTemplateForm
    template_name = 'Employees/partials/deduction_form.html'

    def dispatch(self, request, *args, **kwargs):
        # grab the employee up front
        self.employee = get_object_or_404(EmployeeProfile, pk=kwargs['employee_id'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **ctx):
        ctx = super().get_context_data(**ctx)
        ctx['employee']    = self.employee
        ctx['employee_id'] = self.employee.pk
        return ctx

    def form_valid(self, form):
        form.instance.employee = self.employee
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('employee_list')


class DeductionUpdateView(UpdateView):
    model = DeductionTemplate
    form_class = DeductionTemplateForm
    template_name = 'Employees/partials/deduction_form.html'

    def get_context_data(self, **ctx):
        ctx = super().get_context_data(**ctx)
        ctx['employee']    = self.object.employee
        ctx['employee_id'] = self.object.employee.pk
        return ctx

    def get_success_url(self):
        return reverse_lazy('employee_list')





class DeductionDeleteView(DeleteView):
    model = DeductionTemplate
    template_name = 'Employees/partials/deduction_confirm_delete.html'

    def get_context_data(self, **ctx):
        ctx = super().get_context_data(**ctx)
        ctx['employee']    = self.object.employee
        ctx['employee_id'] = self.object.employee.pk
        return ctx

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()

        # Before deleting the template, check if any snapshot tied to a paid payroll exists
        used_in_paid = DeductionSnapshot.objects.filter(
            template=self.object,
            payroll__is_paid=True
        ).exists()

        if used_in_paid:
            messages.error(
                request,
                "Cannot delete this deduction template because it has already been applied in a paid payroll."
            )
            return HttpResponseRedirect(self.get_success_url())

        # Safe to delete
        response = super().delete(request, *args, **kwargs)
        messages.success(request, "Deduction template deleted successfully.")
        return response

    def get_success_url(self):
        return reverse_lazy('employee_list')










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

# views.py

from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.views import View

class PayrollMarkPaidView(View):
    def get(self, request, pk):
        payroll = get_object_or_404(
            Payroll.objects
                   .prefetch_related('allowance_snapshots', 'deduction_snapshots'),
            pk=pk
        )
        return render(request, "Employees/partials/_payroll_mark_paid_form.html", {
            "payroll": payroll
        })


    def post(self, request, pk):
        payroll = get_object_or_404(Payroll, pk=pk)
        ref = request.POST.get('payment_reference', '').strip()
        payroll.is_paid = True
        payroll.paid_on = timezone.now()
        payroll.payment_reference = ref
        payroll.save(update_fields=['is_paid', 'paid_on', 'payment_reference'])

        messages.success(
            request,
            f"✅ Marked payroll for {payroll.month:%B %Y} as paid (ref: {ref})"
        )
        return redirect('employee_list')


class BulkPayrollMarkPaidView(View):
    def post(self, request):
        month = request.POST.get("month")
        ref = request.POST.get("payment_reference", "").strip()

        if not month:
            messages.error(request, "❌ No payroll month provided.")
            return redirect("employee_list")

        try:
            month_obj = timezone.datetime.strptime(month, "%Y-%m").date()
        except ValueError:
            messages.error(request, "❌ Invalid month format.")
            return redirect("employee_list")

        payrolls = Payroll.objects.filter(month=month_obj)
        to_pay = payrolls.filter(is_paid=False).select_related("employee")
        already_paid = payrolls.filter(is_paid=True)

        # Update unpaid payrolls
        paid_names = []
        for payroll in to_pay:
            payroll.is_paid = True
            payroll.paid_on = now()
            payroll.payment_reference = ref
            payroll.save(update_fields=["is_paid", "paid_on", "payment_reference"])
            paid_names.append(str(payroll.employee))

        if paid_names:
            names_str = ", ".join(paid_names)
            messages.success(
                request,
                f"✅ Marked {len(paid_names)} payroll(s) as paid for {month_obj:%B %Y}: {names_str}"
            )
        else:
            messages.info(request, f"ℹ️ No unpaid payrolls found for {month_obj:%B %Y}.")

        if already_paid.exists():
            messages.warning(request, f"⚠️ {already_paid.count()} payroll(s) were already paid and skipped.")

        return redirect("employee_list")