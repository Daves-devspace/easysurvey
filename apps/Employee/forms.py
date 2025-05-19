import logging
from datetime import datetime
from django import forms

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode


from .models import EmployeeProfile, Payroll, EmployeeSalary, DeductionTemplate, AllowanceTemplate
import secrets
import string

from ..EasyDocs.models import SiteSettings


logger = logging.getLogger(__name__)

def generate_random_password(length=8):
    # Define the characters to choose from
    alphabet = string.ascii_letters + string.digits + string.punctuation
    # Randomly choose characters from the alphabet
    return ''.join(secrets.choice(alphabet) for i in range(length))




from django import forms
from django.contrib.auth.models import User
from .models import EmployeeProfile

# —————————————————————————————
# For superusers (or anyone without an EmployeeProfile)
# —————————————————————————————
class ProfileUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        # include username along with the other core fields
        fields = [
            'username',
            'first_name',
            'last_name',
            'email',
        ]
        widgets = {
            'username':   forms.TextInput(attrs={'class': 'form-control'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name':  forms.TextInput(attrs={'class': 'form-control'}),
            'email':      forms.EmailInput(attrs={'class': 'form-control'}),
        }
        help_texts = {
            'username': None,  # remove the default “<30 characters or fewer…” help text
        }

# —————————————————————————————
# For staff/employees
# —————————————————————————————

class EmployeeProfileUpdateForm(forms.ModelForm):
    # Mirror user fields, all disabled/read‑only
    username   = forms.CharField(disabled=True,
                                 widget=forms.TextInput(attrs={'class':'form-control'}))
    first_name = forms.CharField(disabled=True,
                                 widget=forms.TextInput(attrs={'class':'form-control'}))
    last_name  = forms.CharField(disabled=True,
                                 widget=forms.TextInput(attrs={'class':'form-control'}))
    email      = forms.EmailField(disabled=True,
                                  widget=forms.EmailInput(attrs={'class':'form-control'}))

    # Explicitly declare role & department so we can set disabled here too
    role       = forms.ChoiceField(choices=EmployeeProfile.RoleChoices.choices,
                                   widget=forms.Select(attrs={'class':'form-control'}))
    department = forms.CharField(widget=forms.TextInput(attrs={'class':'form-control'}))

    class Meta:
        model = EmployeeProfile
        fields = [
            'username',
            'first_name',
            'last_name',
            'email',
            'phone_number',
            'address',
            'profile_picture',
            'role',
            'department',
        ]
        widgets = {
            'phone_number':    forms.TextInput(attrs={'class':'form-control'}),
            'address':         forms.Textarea(attrs={'class':'form-control','rows':3}),
            'profile_picture': forms.ClearableFileInput(attrs={'class':'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user')
        super().__init__(*args, **kwargs)

        # Populate the read‑only User fields
        if self.instance and hasattr(self.instance, 'user'):
            u = self.instance.user
            self.fields['username'].initial   = u.username
            self.fields['first_name'].initial = u.first_name
            self.fields['last_name'].initial  = u.last_name
            self.fields['email'].initial      = u.email

        # By default, disable role & department
        self.fields['role'].disabled       = True
        self.fields['department'].disabled = True

        # But if the current user is a superuser, allow editing them
        if user.is_superuser:
            self.fields['role'].disabled       = False
            self.fields['department'].disabled = False










class EmployeeProfileForm(forms.ModelForm):
    first_name = forms.CharField(
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter First Name'})
    )
    last_name = forms.CharField(
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter Last Name'})
    )
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'Enter Email'})
    )

    class Meta:
        model = EmployeeProfile
        fields = ['phone_number', 'department', 'address', 'profile_picture', 'role']
        widgets = {
            'phone_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter Phone Number'}),
            'department': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter Department'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'placeholder': 'Enter Address', 'rows': 3}),
            'profile_picture': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'role': forms.Select(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # If instance exists and has a user, populate user-related fields
        if self.instance and self.instance.pk and hasattr(self.instance, 'user'):
            user = self.instance.user
            self.fields['first_name'].initial = user.first_name
            self.fields['last_name'].initial = user.last_name
            self.fields['email'].initial = user.email

    def save(self, commit=True):
        first_name = self.cleaned_data.pop('first_name')
        last_name = self.cleaned_data.pop('last_name')
        email = self.cleaned_data.pop('email')

        is_update = bool(self.instance and self.instance.pk and hasattr(self.instance, 'user'))

        if is_update:
            user = self.instance.user
            user.first_name = first_name
            user.last_name = last_name
            user.email = email
            user.username = email.split('@')[0]
            user.save()
        else:
            username = email.split('@')[0]
            password = generate_random_password()

            user = User.objects.create(
                username=username,
                email=email,
                first_name=first_name,
                last_name=last_name,
            )
            user.set_password(password)
            user.save()

        profile = super().save(commit=False)
        profile.user = user
        if commit:
            profile.save()

        # --- EMAIL SENDING FOR NEW USER ONLY ---
        if not is_update:
            # Build the reset URL
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            url = reverse('password_reset_confirm', kwargs={'uidb64': uid, 'token': token})
            full_reset = f"{settings.SITE_DOMAIN}{url}"

            # From address
            site = SiteSettings.objects.first()
            # from_email = site.email if site and site.email else settings.DEFAULT_FROM_EMAIL
            from_email = settings.DEFAULT_FROM_EMAIL

            subject = "Your Account Credentials"
            message = (
                f"Hello {first_name},\n\n"
                f"Your account has been created.\n"
                f"Username: {email}\n"
                f"Temporary Password: {password}\n\n"
                f"Please reset your password: {full_reset}\n\n"
                f"Regards,\n{site.company_name if site else 'Company'}"
            )

            try:
                # Log exactly which email settings are in effect
                logger.debug(
                    "EMAIL SETTINGS → host=%r port=%r user=%r use_tls=%r use_ssl=%r",
                    settings.EMAIL_HOST,
                    settings.EMAIL_PORT,
                    settings.EMAIL_HOST_USER,
                    settings.EMAIL_USE_TLS,
                    settings.EMAIL_USE_SSL,
                )
                logger.debug(
                    "Sending email → from=%r to=%r subject=%r",
                    from_email, [email], subject
                )

                send_mail(
                    subject,
                    message,
                    from_email,
                    [email],
                    fail_silently=False,
                )
                logger.info("Invitation email sent to %s", email)

            except Exception as exc:
                logger.error(
                    "Failed to send email: %s", exc, exc_info=True
                )

        return profile



# forms.py snippet


class EmployeeSalaryForm(forms.ModelForm):
    class Meta:
        model = EmployeeSalary
        fields = ['amount', 'effective_from']
        widgets = {
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'effective_from': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        }
        labels = {
            'amount': 'Salary Amount (KES)',
            'effective_from': 'Effective From',
        }


# forms.py
from django import forms
from .models import Payroll

class PayrollMarkPaidForm(forms.ModelForm):
    class Meta:
        model = Payroll
        fields = ['payment_reference', 'is_paid']
        widgets = {
            'payment_reference': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('is_paid') and not cleaned_data.get('payment_reference'):
            self.add_error('payment_reference', 'Payment reference is required when marking as paid.')



# forms.py


DATE_INPUT = forms.DateInput(attrs={
    'type': 'date',
    'class': 'form-control'
})

class AllowanceTemplateForm(forms.ModelForm):
    class Meta:
        model = AllowanceTemplate
        fields = ['name', 'amount', 'recurring', 'start_date', 'end_date']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'recurring': forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'id_recurring_allowance'}),
            'start_date': DATE_INPUT,
            'end_date': DATE_INPUT,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # If not recurring, hide the date fields initially
        if not (self.instance and self.instance.recurring):
            self.fields['start_date'].widget = forms.HiddenInput()
            self.fields['end_date'].widget = forms.HiddenInput()


class DeductionTemplateForm(forms.ModelForm):
    class Meta:
        model = DeductionTemplate
        fields = ['name', 'amount', 'recurring', 'start_date', 'end_date']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'recurring': forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'id_recurring_deduction'}),
            'start_date': DATE_INPUT,
            'end_date': DATE_INPUT,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not (self.instance and self.instance.recurring):
            self.fields['start_date'].widget = forms.HiddenInput()
            self.fields['end_date'].widget = forms.HiddenInput()






