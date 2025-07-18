# views.py
import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import  Prefetch

from django.http import JsonResponse, HttpResponseRedirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, CreateView, UpdateView
from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, get_object_or_404, render

from .models import EmployeeProfile, Payroll
from .forms import EmployeeProfileForm, EmployeeProfileUpdateForm
from .salary.payroll_generator import generate_monthly_payroll

logger = logging.getLogger(__name__)


class EmployeeProfileUpdateView(LoginRequiredMixin, UpdateView):
    model = EmployeeProfile
    form_class = EmployeeProfileUpdateForm
    template_name = 'Employees/profile.html'
    success_url = reverse_lazy('profile')  # adjust this

    def get_object(self):
        return EmployeeProfile.objects.get(user=self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user  # pass user to the form
        return kwargs



def filter_payrolls(request):
    month   = request.GET.get('month')
    is_paid = request.GET.get('is_paid')

    qs = Payroll.objects.all()
    if month:
        year, mon = month.split('-')
        qs = qs.filter(month__year=year, month__month=mon)
    if is_paid == 'true':
        qs = qs.filter(is_paid=True)
    elif is_paid == 'false':
        qs = qs.filter(is_paid=False)

    employees = EmployeeProfile.objects.select_related('user') \
        .prefetch_related(
            Prefetch('payrolls', queryset=qs, to_attr='filtered_payrolls')
        )

    return render(request, 'Employees/partials/_payroll_table_body.html', {
        'employees': employees
    })


# views.py




class EmployeeListView(ListView):
    model = EmployeeProfile
    template_name = 'Employees/employee_management.html'
    context_object_name = 'employees'

    def get_queryset(self):
        latest_payrolls = Payroll.objects.order_by('-month')
        return EmployeeProfile.objects.select_related('user') \
            .filter(user__is_superuser=False) \
            .prefetch_related(
            Prefetch('payrolls', queryset=latest_payrolls, to_attr='latest_payrolls')
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        employees = context['employees']

        context['add_form'] = EmployeeProfileForm()

        context['total_payable'] = sum(e.latest_net_salary() for e in employees)
        context['total_allowances'] = sum(e.latest_total_allowances() for e in employees)
        context['total_deductions'] = sum(e.latest_total_deductions() for e in employees)

        context['all_last_payrolls_paid'] = all(
            (e.latest_payroll().is_paid if e.latest_payroll() else True) for e in employees
        )

        employees_with_payroll = [
            e for e in employees if e.latest_payrolls
        ]
        context['employees_with_payroll'] = employees_with_payroll

        # ✅ Always set edit_form and edit_employee_id
        edit_id = self.request.GET.get('edit')
        if edit_id:
            try:
                employee = get_object_or_404(EmployeeProfile, pk=edit_id)
                context['edit_form'] = EmployeeProfileForm(instance=employee)
                context['edit_employee_id'] = employee.pk
            except Exception as e:
                context['edit_form'] = None
                context['edit_employee_id'] = None
        else:
            context['edit_form'] = None
            context['edit_employee_id'] = None

        return context




class EmployeeCreateView(CreateView):
    model = EmployeeProfile
    form_class = EmployeeProfileForm
    template_name = 'Employees/employee_management.html'
    success_url = reverse_lazy('employee_list')

    @transaction.atomic
    def form_valid(self, form):
        try:
            employee = form.save()
            messages.success(self.request, f"✅ Employee '{employee}' added.")
            return redirect(self.success_url)
        except Exception as e:
            transaction.set_rollback(True)
            messages.error(self.request, f"❌ Failed to add employee: {e}")
            return self.form_invalid(form)

    def form_invalid(self, form):
        messages.error(self.request, "❌ Please correct the errors below.")
        # Re-render list with form errors
        return render(self.request, 'Employees/employee_management.html', {
            'add_form': form,
            # Add other context vars if needed
        })


class EmployeeUpdateView(UpdateView):
    model = EmployeeProfile
    form_class = EmployeeProfileForm
    pk_url_kwarg = 'pk'
    template_name = 'Employees/employee_management.html'
    success_url = reverse_lazy('employee_list')

    @transaction.atomic
    def form_valid(self, form):
        try:
            employee = form.save()
            messages.success(self.request, f"✅ Employee '{employee}' updated.")
            return HttpResponseRedirect(self.get_success_url())  # ✅ redirect instead of rendering

        except Exception as e:
            transaction.set_rollback(True)
            messages.error(self.request, f"❌ Failed to update employee: {e}")
            return self.form_invalid(form)

    def form_invalid(self, form):
        messages.error(self.request, "❌ Please correct the errors below.")
        return EmployeeListView.as_view()(self.request)


# views.py



def payroll_detail(request, pk):
    payroll = get_object_or_404(Payroll, pk=pk)
    allowances = list(payroll.allowances.values('id', 'name', 'amount', 'recurring'))
    deductions = list(payroll.deductions.values('id', 'name', 'amount', 'recurring'))
    return JsonResponse({
        'id': payroll.id,
        'basic_salary': payroll.basic_salary,
        'pay_for_month': payroll.pay_for_month.strftime('%Y-%m'),
        'allowances': allowances,
        'deductions': deductions,
    })







class PayrollGenerateAllView(View):
    def post(self, request):
        today     = timezone.now().date()
        new_month = today.replace(day=1)

        # 1) Make sure there's no outstanding unpaid payroll anywhere
        unpaid_qs = Payroll.objects.filter(is_paid=False)
        if unpaid_qs.exists():
            names = [p.employee.user.get_full_name() for p in unpaid_qs]
            messages.error(
                request,
                "⚠️ Cannot generate new payrolls until these are paid: "
                + ", ".join(names)
            )
            return redirect('employee_list')

        # 2) Fire off your generator — it will return the # of new Payroll objects
        count = generate_monthly_payroll(today)

        if count > 0:
            messages.success(
                request,
                f"✅ Generated {count} payroll{'s' if count > 1 else ''} for {new_month:%B %Y}."
            )
        else:
            # 3) No new payrolls — but *some* employees might already have one for this month.
            up_to_date_qs = EmployeeProfile.objects.filter(
                payrolls__month=new_month
            ).distinct()
            if up_to_date_qs.exists():
                names = ", ".join(e.user.get_full_name() for e in up_to_date_qs)
                messages.info(
                    request,
                    f"ℹ️ Employees already up‑to‑date for {new_month:%B %Y}: {names}."
                )
            else:
                messages.info(
                    request,
                    f"ℹ️ No payrolls exist yet for {new_month:%B %Y}."
                )

        return redirect('employee_list')




