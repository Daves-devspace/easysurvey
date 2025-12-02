from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.models import User
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.generic import UpdateView, ListView, TemplateView
from django.shortcuts import render 
from apps.Employee.forms import EmployeeProfileForm, EmployeeProfileUpdateForm
from apps.Employee.models import EmployeeProfile


from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.views.generic.edit import UpdateView
from django.shortcuts import redirect
from django.contrib import messages

from .forms import UnifiedEmployeeProfileForm
from .models import EmployeeProfile
from django.contrib.auth.models import User

# ——————————————————————————
# Employee / Superuser profile view
# ——————————————————————————
class EmployeeProfileDashboardView(LoginRequiredMixin, UpdateView):
    model = EmployeeProfile
    form_class = UnifiedEmployeeProfileForm
    template_name = 'Employees/profile.html'
    success_url = reverse_lazy('employee-dashboard')

    def get_object(self):
        # Always ensure EmployeeProfile exists
        profile, created = EmployeeProfile.objects.get_or_create(user=self.request.user)
        return profile

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

# ——————————————————————————
# Admin / Superuser management view
# ——————————————————————————
class AdminManagementView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'Home/adminmanagement.html'
    success_url = reverse_lazy('admin-management')

    def test_func(self):
        return self.request.user.is_superuser

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['users'] = User.objects.all()
        ctx['profile_form'] = UnifiedEmployeeProfileForm(instance=self.request.user.employeeprofile, user=self.request.user)
        return ctx

    def post(self, request, *args, **kwargs):
        if 'profile_submit' in request.POST:
            profile_instance = request.user.employeeprofile
            form = UnifiedEmployeeProfileForm(request.POST, request.FILES, instance=profile_instance, user=request.user)
            if form.is_valid():
                form.save()
                messages.success(request, "Profile updated successfully.")
                return redirect(self.success_url)
            return self.render_to_response(self.get_context_data(profile_form=form))

        elif 'toggle_user' in request.POST:
            target_id = request.POST.get('toggle_user')
            user = get_object_or_404(User, id=target_id)
            if user != request.user:
                user.is_active = not user.is_active
                user.save()
            return redirect(request.path)

        return super().get(request, *args, **kwargs)



class UserManagementView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'Home/user_management.html'
    success_url = reverse_lazy('user-management')

    def test_func(self):
        # Only superusers can access
        return self.request.user.is_superuser

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # List of all users
        context['users'] = User.objects.all().order_by('username')
        # If editing a specific user, include the form
        user_id = self.request.GET.get('edit')
        if user_id:
            target_user = get_object_or_404(User, id=user_id)
            profile, _ = EmployeeProfile.objects.get_or_create(user=target_user)
            context['profile_form'] = UnifiedEmployeeProfileForm(instance=profile, user=self.request.user)
            context['editing_user'] = target_user
        return context

    def post(self, request, *args, **kwargs):
        # Handle profile updates
        if 'profile_submit' in request.POST:
            user_id = request.POST.get('user_id')
            target_user = get_object_or_404(User, id=user_id)
            profile, _ = EmployeeProfile.objects.get_or_create(user=target_user)
            form = UnifiedEmployeeProfileForm(request.POST, request.FILES, instance=profile, user=request.user)
            if form.is_valid():
                form.save()
                messages.success(request, f"Profile for '{target_user.username}' updated successfully.")
                return redirect(self.success_url)
            else:
                # Re-render page with form errors
                context = self.get_context_data()
                context['profile_form'] = form
                context['editing_user'] = target_user
                return render(request, self.template_name, context)

        # Toggle user activation
        elif 'toggle_user' in request.POST:
            user_id = request.POST.get('toggle_user')
            target_user = get_object_or_404(User, id=user_id)
            if target_user != request.user:  # prevent self-deactivation
                target_user.is_active = not target_user.is_active
                target_user.save()
                status = "activated" if target_user.is_active else "deactivated"
                messages.success(request, f"User '{target_user.username}' has been {status}.")
            else:
                messages.error(request, "You cannot deactivate yourself.")
            return redirect(self.success_url)

        return super().get(request, *args, **kwargs)