from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.models import User
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.utils.decorators import method_decorator
from django.views.generic import UpdateView, ListView

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
class UserProfileUpdateView(LoginRequiredMixin, UpdateView):
    model = User
    form_class = ProfileUpdateForm
    template_name = 'Home/super_profile_form.html'
    success_url = '/dashboard/'

    def get_object(self):
        return self.request.user


@method_decorator(staff_member_required, name='dispatch')
class AdminUserListView(ListView):
    model = User
    template_name = 'application/user-list.html'
    context_object_name = 'users'

@user_passes_test(lambda u: u.is_superuser)
def toggle_user_active(request, user_id):
    user = get_object_or_404(User, id=user_id)
    if user != request.user:
        user.is_active = not user.is_active
        user.save()
    return redirect('admin-user-list')