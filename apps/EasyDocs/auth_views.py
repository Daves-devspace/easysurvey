# yourapp/views.py
import logging

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.views import PasswordResetView, PasswordResetConfirmView, LoginView
from django.contrib.messages.views import SuccessMessageMixin
from django.http import Http404
from django.shortcuts import render, redirect
from django.contrib import messages
from django.urls import reverse_lazy
from django.views.generic import TemplateView

from apps.EasyDocs.forms import CustomPasswordResetForm, CustomSetPasswordForm, CustomAuthenticationForm
from apps.Employee.models import EmployeeProfile
from apps.tenants.support_access import get_company_for_schema, support_access_is_enabled
from django.db import connection
logger = logging.getLogger(__name__)

# Any view for testing
def test_404(request):
    raise Http404("Testing custom 404 page.")
class LandingPageView(TemplateView):
    template_name = 'Home/index.html'





import logging
from django.contrib.auth.views import LoginView
from django.urls import reverse_lazy

logger = logging.getLogger(__name__)

class CustomLoginView(LoginView):
    template_name               = 'Home/login.html'
    authentication_form         = CustomAuthenticationForm
    redirect_authenticated_user = True
    success_url                 = reverse_lazy('home')

    def dispatch(self, request, *args, **kwargs):
        logger.debug("CustomLoginView.dispatch: method=%s, user=%s, authenticated=%s",
                     request.method,
                     request.user,
                     request.user.is_authenticated)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        # Called when the credentials are correct
        user = form.get_user()

        profile = getattr(user, 'employeeprofile', None)
        if profile and profile.role == EmployeeProfile.RoleChoices.IT_SUPPORT:
            company = get_company_for_schema(connection.schema_name)
            if company and not support_access_is_enabled(company):
                messages.error(
                    self.request,
                    "IT Support access is currently disabled for this tenant. Ask a tenant admin to grant temporary support access.",
                )
                return redirect('login')

        if profile and profile.force_password_reset:
            if not user.email:
                messages.error(self.request, "Password reset is required, but no email is configured for your account.")
                return self.form_invalid(form)

            reset_form = CustomPasswordResetForm({'email': user.email})
            if reset_form.is_valid():
                reset_form.save(
                    request=self.request,
                    use_https=self.request.is_secure(),
                    from_email=None,
                )
                messages.warning(
                    self.request,
                    "Password reset is required before login. A reset link has been sent to your email.",
                )
                return redirect('password_reset_done')

            messages.error(self.request, "Password reset is required. Please use the password reset form.")
            return redirect('password_reset')

        logger.info("CustomLoginView.form_valid: logging in user=%s (id=%s)", user.username, user.pk)

        response = super().form_valid(form)

        # After login, where are we redirecting?
        next_url = self.get_redirect_url()
        logger.debug("CustomLoginView.form_valid: redirecting to next_url=%s", next_url or str(self.success_url))

        return response

    def form_invalid(self, form):
        # Called when credentials fail validation
        logger.warning("CustomLoginView.form_invalid: login failed for username=%s; errors=%s",
                       form.cleaned_data.get('username', '<none>'),
                       form.errors.as_json())
        return super().form_invalid(form)

    def get_success_url(self):
        url = super().get_success_url() or str(self.success_url)
        logger.debug("CustomLoginView.get_success_url: resolved success_url=%s", url)
        return url



# def custom_login(request):
#     # 1) grab and remove any stashed username
#     prefill_username = request.session.pop('prefill_username', '')
#
#     if request.method == 'POST':
#         # 2) get whatever the user just typed
#         username = request.POST.get('username')
#         password = request.POST.get('password')
#
#         user = authenticate(request, username=username, password=password)
#         if user is not None:
#             login(request, user)
#             return redirect('home')
#         else:
#             messages.error(request, 'Invalid username or password.')
#             # 3) if login failed, re‑use what they typed
#             prefill_username = username
#
#     # 4) on GET or after a failed POST, render login.html
#     return render(request, 'Home/login.html', {
#         'prefill_username': prefill_username
#     })



def logout_view(request):
    logout(request)
    return redirect('login')


class CustomPasswordResetView(SuccessMessageMixin, PasswordResetView):

    form_class = CustomPasswordResetForm
    template_name = 'application/password-reset.html'
    email_template_name = 'application/password_reset_email.html'
    subject_template_name = 'application/password_reset_subject.txt'
    success_url = reverse_lazy('password_reset_done')
    success_message = "We've emailed you instructions for setting your password. …"



    def dispatch(self, request, *args, **kwargs):
        print("CustomPasswordResetView dispatch called")
        logger.debug("PRV.dispatch ➞ %s %s", request.method, request.path)
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        logger.debug("PRV.get ➞ rendering form")
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        logger.debug("PRV.post ➞ data=%s", request.POST.dict())
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        logger.debug("PRV.form_valid ➞ valid email=%s", form.cleaned_data.get('email'))
        return super().form_valid(form)

    def form_invalid(self, form):
        logger.warning("PRV.form_invalid ➞ errors=%s", form.errors.as_json())
        return super().form_invalid(form)








class CustomPasswordResetConfirmView(PasswordResetConfirmView):
    form_class = CustomSetPasswordForm
    template_name = 'application/password_reset_confirm.html'
    success_url = reverse_lazy('password_reset_complete')

    def form_valid(self, form):
        logger.info(
            "PasswordResetConfirm.form_valid: resetting password for user=%s (id=%s)",
            self.user.username, self.user.pk,
        )
        logger.info(
            "CustomPasswordResetConfirmView.form_valid: password reset confirmed for user=%s (id=%s)",
            self.user.get_username(), self.user.pk,
        )
        response = super().form_valid(form)
        profile = getattr(self.user, 'employeeprofile', None)
        if profile and profile.force_password_reset:
            profile.force_password_reset = False
            profile.save(update_fields=['force_password_reset'])
        logger.info(
            "PasswordResetConfirm.form_valid: password reset complete for user=%s (id=%s)",
            self.user.username, self.user.pk,
        )
        # Save username in session to prefill on login
        self.request.session['prefill_username'] = self.user.get_username()
        return response

    def form_invalid(self, form):
        logger.warning(
            "PasswordResetConfirm.form_invalid: errors=%s",
            form.errors.as_json(),
        )
        return super().form_invalid(form)


