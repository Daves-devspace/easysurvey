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
from apps.EasyDocs.forms import CustomPasswordResetForm
from django.db import connection
from django.utils import timezone
from django_tenants.utils import schema_context
from apps.tenants.models import Company
from apps.tenants.support_access import get_company_for_schema, support_access_is_enabled
from apps.EasyDocs.models import AuditLog
from datetime import timedelta

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
        ctx['users'] = User.objects.select_related('employeeprofile').order_by('username')
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
    success_url = reverse_lazy('users-update')

    def test_func(self):
        # Only superusers can access
        return self.request.user.is_superuser

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        company = self._sync_it_support_user_state()
        # List of all users
        context['users'] = User.objects.select_related('employeeprofile').order_by('username')
        context['support_company'] = company
        context['support_modes'] = Company.SupportAccessMode.choices
        context['support_access_enabled'] = support_access_is_enabled(company)
        # If editing a specific user, include the form
        user_id = self.request.GET.get('edit')
        if user_id:
            target_user = get_object_or_404(User, id=user_id)
            profile, _ = EmployeeProfile.objects.get_or_create(user=target_user)
            context['profile_form'] = UnifiedEmployeeProfileForm(instance=profile, user=self.request.user)
            context['editing_user'] = target_user
        return context

    def _role_label(self, user):
        profile = getattr(user, 'employeeprofile', None)
        return getattr(profile, 'role', None)

    def _company_in_public_schema(self):
        return get_company_for_schema(connection.schema_name)

    def _log_support_audit(self, description):
        try:
            AuditLog.objects.create(
                user=self.request.user,
                action='config_change',
                model_name='SupportAccess',
                object_id=str(connection.schema_name),
                description=description,
                ip_address=self.request.META.get('REMOTE_ADDR'),
                user_agent=self.request.META.get('HTTP_USER_AGENT'),
            )
        except Exception:
            pass

    def _sync_it_support_user_state(self):
        company = self._company_in_public_schema()
        if not company:
            return None

        should_enable = support_access_is_enabled(company)
        it_users = User.objects.filter(employeeprofile__role=EmployeeProfile.RoleChoices.IT_SUPPORT)
        it_users.exclude(is_active=should_enable).update(is_active=should_enable)
        return company

    def _support_user_queryset(self):
        return User.objects.filter(employeeprofile__role=EmployeeProfile.RoleChoices.IT_SUPPORT).select_related('employeeprofile')

    def _total_users_in_role(self, role):
        return User.objects.filter(employeeprofile__role=role).count()

    def _active_users_in_role(self, role):
        return User.objects.filter(
            is_active=True,
            employeeprofile__role=role,
        ).count()

    def _can_toggle_user(self, actor, target_user):
        if target_user == actor:
            return False, "You cannot deactivate yourself."

        role = self._role_label(target_user)

        if not target_user.is_active:
            if role == EmployeeProfile.RoleChoices.IT_SUPPORT:
                company = self._company_in_public_schema()
                if company and not support_access_is_enabled(company) and company.support_access_mode != Company.SupportAccessMode.ALWAYS:
                    return False, "Use Grant Temporary Support Access to enable IT Support for this tenant."
            return True, ""

        if role == EmployeeProfile.RoleChoices.IT_SUPPORT:
            company = self._company_in_public_schema()
            if company and company.support_access_mode != Company.SupportAccessMode.ALWAYS:
                return True, ""
            if self._active_users_in_role(EmployeeProfile.RoleChoices.IT_SUPPORT) <= 1:
                return False, "Cannot deactivate the last active IT Support user."
        if role == EmployeeProfile.RoleChoices.ADMIN:
            if self._active_users_in_role(EmployeeProfile.RoleChoices.ADMIN) <= 1:
                return False, "Cannot deactivate the last active Admin user."

        return True, ""

    def _can_change_role(self, target_user, old_role, new_role):
        """Block role updates that would remove the last active critical role holder."""
        if old_role == new_role:
            return True, ""

        if not target_user.is_active:
            return True, ""

        if old_role == EmployeeProfile.RoleChoices.IT_SUPPORT and new_role != EmployeeProfile.RoleChoices.IT_SUPPORT:
            if self._total_users_in_role(EmployeeProfile.RoleChoices.IT_SUPPORT) <= 1:
                return False, "Cannot reassign the last configured IT Support user to another role."

        if old_role == EmployeeProfile.RoleChoices.ADMIN and new_role != EmployeeProfile.RoleChoices.ADMIN:
            if self._active_users_in_role(EmployeeProfile.RoleChoices.ADMIN) <= 1:
                return False, "Cannot reassign the last active Admin user to another role."

        return True, ""

    def post(self, request, *args, **kwargs):
        if 'support_policy_submit' in request.POST:
            company = self._company_in_public_schema()
            if not company:
                messages.error(request, 'Could not load tenant support policy.')
                return redirect(self.success_url)

            new_mode = request.POST.get('support_access_mode', Company.SupportAccessMode.ON_REQUEST)
            reason = request.POST.get('support_access_reason', '').strip()
            if new_mode not in dict(Company.SupportAccessMode.choices):
                messages.error(request, 'Invalid support access mode.')
                return redirect(self.success_url)

            with schema_context('public'):
                public_company = Company.objects_with_deleted.get(pk=company.pk)
                public_company.support_access_mode = new_mode
                public_company.support_access_reason = reason
                public_company.support_access_updated_by = request.user.username
                if new_mode == Company.SupportAccessMode.ALWAYS:
                    public_company.support_access_until = None
                elif new_mode == Company.SupportAccessMode.DISABLED:
                    public_company.support_access_until = None
                public_company.save(update_fields=['support_access_mode', 'support_access_reason', 'support_access_updated_by', 'support_access_until'])

            company = self._sync_it_support_user_state()
            self._log_support_audit(f'Support access mode changed to {new_mode}. Reason: {reason or "—"}')
            messages.success(request, 'Support privacy policy updated.')
            if new_mode != Company.SupportAccessMode.ALWAYS:
                messages.info(request, 'IT Support accounts now require explicit grant before access when the window is closed.')
            return redirect(self.success_url)

        elif 'grant_support_access' in request.POST:
            company = self._company_in_public_schema()
            if not company:
                messages.error(request, 'Could not load tenant support policy.')
                return redirect(self.success_url)

            hours_raw = request.POST.get('support_access_hours', '24')
            reason = request.POST.get('support_access_reason', '').strip() or 'Tenant-approved support access'
            try:
                hours = max(1, min(int(hours_raw), 168))
            except (TypeError, ValueError):
                hours = 24

            with schema_context('public'):
                public_company = Company.objects_with_deleted.get(pk=company.pk)
                public_company.support_access_until = timezone.now() + timedelta(hours=hours)
                public_company.support_access_reason = reason
                public_company.support_access_updated_by = request.user.username
                public_company.save(update_fields=['support_access_until', 'support_access_reason', 'support_access_updated_by'])

            self._sync_it_support_user_state()

            sent = []
            failed = []
            for support_user in self._support_user_queryset():
                profile = getattr(support_user, 'employeeprofile', None)
                if profile:
                    profile.force_password_reset = True
                    profile.save(update_fields=['force_password_reset'])

                if not support_user.email:
                    failed.append(support_user.username)
                    continue

                reset_form = CustomPasswordResetForm({'email': support_user.email})
                if reset_form.is_valid():
                    reset_form.save(request=request, use_https=request.is_secure(), from_email=None)
                    sent.append(support_user.email)
                else:
                    failed.append(support_user.email)

            self._log_support_audit(f'Support access granted for {hours} hour(s). Reason: {reason}')
            messages.success(request, f'Support access granted for {hours} hour(s).')
            if sent:
                messages.success(request, f'Reset links sent to: {", ".join(sent)}.')
            if failed:
                messages.warning(request, f'Could not send reset links to: {", ".join(failed)}.')
            return redirect(self.success_url)

        elif 'revoke_support_access' in request.POST:
            company = self._company_in_public_schema()
            if not company:
                messages.error(request, 'Could not load tenant support policy.')
                return redirect(self.success_url)

            if company.support_access_mode == Company.SupportAccessMode.ALWAYS:
                messages.error(request, 'Change support policy from Always Allowed before revoking support access.')
                return redirect(self.success_url)

            reason = request.POST.get('support_access_reason', '').strip() or 'Tenant revoked support access'
            with schema_context('public'):
                public_company = Company.objects_with_deleted.get(pk=company.pk)
                public_company.support_access_until = None
                public_company.support_access_reason = reason
                public_company.support_access_updated_by = request.user.username
                public_company.save(update_fields=['support_access_until', 'support_access_reason', 'support_access_updated_by'])

            self._sync_it_support_user_state()
            self._log_support_audit(f'Support access revoked. Reason: {reason}')
            messages.success(request, 'Support access revoked and IT Support users deactivated for this tenant.')
            return redirect(self.success_url)

        # Handle profile updates
        if 'profile_submit' in request.POST:
            user_id = request.POST.get('user_id')
            target_user = get_object_or_404(User, id=user_id)
            profile, _ = EmployeeProfile.objects.get_or_create(user=target_user)
            old_role = profile.role
            form = UnifiedEmployeeProfileForm(request.POST, request.FILES, instance=profile, user=request.user)
            if form.is_valid():
                new_role = form.cleaned_data.get('role', old_role)
                allowed, reason = self._can_change_role(target_user, old_role, new_role)
                if not allowed:
                    messages.error(request, reason)
                    return redirect(self.success_url)
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
            allowed, reason = self._can_toggle_user(request.user, target_user)
            if allowed:
                target_user.is_active = not target_user.is_active
                target_user.save()
                status = "activated" if target_user.is_active else "deactivated"
                messages.success(request, f"User '{target_user.username}' has been {status}.")
            else:
                messages.error(request, reason)
            return redirect(self.success_url)

        elif 'send_reset' in request.POST:
            user_id = request.POST.get('send_reset')
            target_user = get_object_or_404(User, id=user_id)
            if not target_user.email:
                messages.error(request, f"User '{target_user.username}' has no email address.")
                return redirect(self.success_url)

            reset_form = CustomPasswordResetForm({'email': target_user.email})
            if reset_form.is_valid():
                reset_form.save(
                    request=request,
                    use_https=request.is_secure(),
                    from_email=None,
                )
                messages.success(request, f"Password reset link sent to '{target_user.email}'.")
            else:
                messages.error(request, f"Could not send reset for '{target_user.username}': {reset_form.errors.as_text()}")
            return redirect(self.success_url)

        elif 'force_reset' in request.POST:
            user_id = request.POST.get('force_reset')
            target_user = get_object_or_404(User, id=user_id)
            profile, _ = EmployeeProfile.objects.get_or_create(user=target_user)

            if not target_user.email:
                messages.error(request, f"User '{target_user.username}' has no email address.")
                return redirect(self.success_url)

            profile.force_password_reset = True
            profile.save(update_fields=['force_password_reset'])

            reset_form = CustomPasswordResetForm({'email': target_user.email})
            if reset_form.is_valid():
                reset_form.save(
                    request=request,
                    use_https=request.is_secure(),
                    from_email=None,
                )
                messages.success(request, f"Forced reset enabled and reset link sent to '{target_user.email}'.")
            else:
                messages.warning(request, f"Forced reset enabled, but email send failed for '{target_user.username}'.")
            return redirect(self.success_url)

        return super().get(request, *args, **kwargs)