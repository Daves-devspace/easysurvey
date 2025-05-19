from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.models import User
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.generic import UpdateView, ListView, TemplateView

from apps.Employee.forms import EmployeeProfileForm, EmployeeProfileUpdateForm
from apps.Employee.models import EmployeeProfile


from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.views.generic.edit import UpdateView
from django.shortcuts import redirect
from django.contrib import messages

from .forms import ProfileUpdateForm, EmployeeProfileUpdateForm
from .models import EmployeeProfile
from django.contrib.auth.models import User

# ——————————————————————
#  Staff / Employee
# ——————————————————————
class EmployeeProfileDashboardView(LoginRequiredMixin, UpdateView):
    model = EmployeeProfile
    form_class = EmployeeProfileUpdateForm
    template_name = 'Employees/profile.html'
    success_url = '/dashboard/'

    def dispatch(self, request, *args, **kwargs):
        # if no EmployeeProfile, redirect to user-profile-update
        if not hasattr(request.user, 'employeeprofile'):
            return redirect('user-profile-update')
        return super().dispatch(request, *args, **kwargs)

    def get_object(self):
        try:
            return self.request.user.employeeprofile
        except EmployeeProfile.DoesNotExist:
            raise Http404("Employee profile not found.")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = context['object']  # same as self.get_object()
        context['payrolls'] = profile.payrolls.order_by('-month')
        return context

# ——————————————————————
#  Superuser (or any user w/o EmployeeProfile)
# ——————————————————————
def superuser_required(user):
    return user.is_superuser


class AdminManagementView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'Home/adminmanagement.html'
    success_url = reverse_lazy('user-profile-update')

    def test_func(self):
        return self.request.user.is_superuser

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # profile form bound to current user
        ctx['profile_form'] = ProfileUpdateForm(instance=self.request.user)
        # list of all users to manage
        ctx['users'] = User.objects.all()
        return ctx

    def post(self, request, *args, **kwargs):
        # Distinguish which form was submitted by looking at a hidden field
        if 'profile_submit' in request.POST:
            form = ProfileUpdateForm(request.POST, instance=request.user)
            if form.is_valid():
                form.save()
                messages.success(request, "Your profile has been updated.")
                return redirect(self.success_url)
            else:
                # re‑render with errors
                return self.render_to_response(self.get_context_data(profile_form=form))

        elif 'toggle_user' in request.POST:
            target_id = request.POST.get('toggle_user')
            user = get_object_or_404(User, id=target_id)
            if user != request.user:
                user.is_active = not user.is_active
                user.save()
            return redirect(request.path)

        # fallback
        return super().get(request, *args, **kwargs)