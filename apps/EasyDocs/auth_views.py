# yourapp/views.py
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.views import PasswordResetView, PasswordResetConfirmView
from django.contrib.messages.views import SuccessMessageMixin
from django.http import Http404
from django.shortcuts import render, redirect
from django.contrib import messages
from django.urls import reverse_lazy

from apps.EasyDocs.forms import CustomPasswordResetForm, CustomSetPasswordForm

# Any view for testing
def test_404(request):
    raise Http404("Testing custom 404 page.")


def custom_login(request):
    # 1) grab and remove any stashed username
    prefill_username = request.session.pop('prefill_username', '')

    if request.method == 'POST':
        # 2) get whatever the user just typed
        username = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect('home')
        else:
            messages.error(request, 'Invalid username or password.')
            # 3) if login failed, re‑use what they typed
            prefill_username = username

    # 4) on GET or after a failed POST, render login.html
    return render(request, 'Home/login.html', {
        'prefill_username': prefill_username
    })



def logout_view(request):
    logout(request)
    return redirect('login')


class CustomPasswordResetView(SuccessMessageMixin, PasswordResetView):
    form_class = CustomPasswordResetForm
    template_name = 'application/password-reset.html'
    email_template_name = 'application/password_reset_email.html'
    subject_template_name = 'application/password_reset_subject.txt'
    success_url = reverse_lazy('password_reset_done')
    success_message = "We’ve emailed you instructions for setting    your password. If an account exists with the email you entered, you should receive them shortly."


class PasswordResetDoneView:
    pass


class CustomPasswordResetConfirmView(PasswordResetConfirmView):
    form_class = CustomSetPasswordForm
    template_name = 'application/password_reset_confirm.html'
    success_url = reverse_lazy('password_reset_complete')

    def form_valid(self, form):
        response = super().form_valid(form)
        # Save username in session to prefill on login
        self.request.session['prefill_username'] = self.user.get_username()
        return response


class PasswordResetCompleteView:
    pass