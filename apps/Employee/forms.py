import logging
from datetime import datetime
from urllib.parse import urljoin
from django import forms

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode


from .models import EmployeeProfile, Payroll, EmployeeSalary, DeductionTemplate, AllowanceTemplate
import secrets
import string

from ..EasyDocs.models import SiteSettings


logger = logging.getLogger(__name__)


def _validate_unique_user_email(email_value, current_user=None):
    normalized_email = str(email_value or "").strip().lower()
    if not normalized_email:
        raise forms.ValidationError("Email is required.")

    existing_users = User.objects.filter(email__iexact=normalized_email)
    if current_user is not None and getattr(current_user, "pk", None):
        existing_users = existing_users.exclude(pk=current_user.pk)

    if existing_users.exists():
        raise forms.ValidationError("A user with this email already exists.")

    return normalized_email

def generate_random_password(length=8):
    # Define the characters to choose from
    alphabet = string.ascii_letters + string.digits + string.punctuation
    # Randomly choose characters from the alphabet
    return ''.join(secrets.choice(alphabet) for i in range(length))




from django import forms
from django.contrib.auth.models import User
from .models import EmployeeProfile
from django import forms
from django.contrib.auth.models import User
from .models import EmployeeProfile

class UnifiedEmployeeProfileForm(forms.ModelForm):
    # User fields
    username = forms.CharField(widget=forms.TextInput(attrs={'class': 'form-control'}))
    first_name = forms.CharField(widget=forms.TextInput(attrs={'class': 'form-control'}))
    last_name = forms.CharField(widget=forms.TextInput(attrs={'class': 'form-control'}))
    email = forms.EmailField(widget=forms.EmailInput(attrs={'class': 'form-control'}))

    class Meta:
        model = EmployeeProfile
        fields = [
            'username', 'first_name', 'last_name', 'email',  # User fields
            'phone_number', 'address', 'department', 'role', 'profile_picture'  # EmployeeProfile fields
        ]
        widgets = {
            'phone_number': forms.TextInput(attrs={'class': 'form-control'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'department': forms.TextInput(attrs={'class': 'form-control'}),
            'role': forms.Select(attrs={'class': 'form-control'}),
            'profile_picture': forms.ClearableFileInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        # pop 'user' if passed, otherwise None
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        # populate user fields from related User
        if self.instance and hasattr(self.instance, 'user'):
            u = self.instance.user
            self.fields['username'].initial = u.username
            self.fields['first_name'].initial = u.first_name
            self.fields['last_name'].initial = u.last_name
            self.fields['email'].initial = u.email

        # Permission checks (only if user is provided)
        if self.user:
            if self.user.is_superuser:
                for field in self.fields:
                    self.fields[field].disabled = False
                if self.user == self.instance.user:
                    self.fields['role'].disabled = True
            else:
                for field in ['address', 'department', 'role',
                              'first_name', 'last_name', 'email']:
                    self.fields[field].disabled = True
                self.fields['username'].disabled = False
        else:
            # Admin default behavior: enable all fields
            for field in self.fields:
                self.fields[field].disabled = False

    def clean_email(self):
        current_user = None
        if self.instance and hasattr(self.instance, 'user'):
            current_user = self.instance.user
        return _validate_unique_user_email(self.cleaned_data.get('email'), current_user)

    def save(self, commit=True):
        profile = super().save(commit=False)
        
        # Save the related User fields
        if hasattr(profile, 'user') and profile.user:
            user = profile.user
            user.username = self.cleaned_data['username']
            user.first_name = self.cleaned_data['first_name']
            user.last_name = self.cleaned_data['last_name']
            user.email = self.cleaned_data['email']
            if commit:
                user.save(update_fields=['username', 'first_name', 'last_name', 'email'])
        
        # Save the profile picture if uploaded
        if 'profile_picture' in self.cleaned_data and self.cleaned_data['profile_picture']:
            profile.profile_picture = self.cleaned_data['profile_picture']

        if commit:
            profile.save()

        return profile


# —————————————————————————————
# For staff/employees
# —————————————————————————————

class EmployeeProfileUpdateForm(forms.ModelForm):
    # Mirror user fields
    username   = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    first_name = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    last_name  = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    email      = forms.EmailField(
        widget=forms.EmailInput(attrs={'class': 'form-control'})
    )

    role = forms.ChoiceField(
        choices=EmployeeProfile.RoleChoices.choices,
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    department = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )

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
            'phone_number':    forms.TextInput(attrs={'class': 'form-control'}),
            'address':         forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'profile_picture': forms.ClearableFileInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user')  # currently logged-in user
        super().__init__(*args, **kwargs)

        # Populate user fields from related User model
        if self.instance and hasattr(self.instance, 'user'):
            u = self.instance.user
            self.fields['username'].initial   = u.username
            self.fields['first_name'].initial = u.first_name
            self.fields['last_name'].initial  = u.last_name
            self.fields['email'].initial      = u.email

        # Disable all user and employee fields by default
        self.fields['username'].disabled   = True
        self.fields['first_name'].disabled = True
        self.fields['last_name'].disabled  = True
        self.fields['email'].disabled      = True
        self.fields['role'].disabled       = True
        self.fields['department'].disabled = True

        # Grant editing rights based on user's privileges and ownership
        if user.is_superuser and user != self.instance.user:
            # Superuser editing someone else — enable all fields
            self.fields['username'].disabled   = False
            self.fields['first_name'].disabled = False
            self.fields['last_name'].disabled  = False
            self.fields['email'].disabled      = False
            self.fields['role'].disabled       = False
            self.fields['department'].disabled = False

        elif user.is_superuser and user == self.instance.user:
            # Superuser editing their own profile — enable all except role
            self.fields['username'].disabled   = False
            self.fields['first_name'].disabled = False
            self.fields['last_name'].disabled  = False
            self.fields['email'].disabled      = False
            self.fields['department'].disabled = False
            self.fields['role'].disabled       = True

        elif user == self.instance.user:
            # Regular user editing their own profile — enable user fields only
            self.fields['username'].disabled   = False
            self.fields['first_name'].disabled = True
            self.fields['last_name'].disabled  = True
            self.fields['email'].disabled      = True

    def clean_email(self):
        current_user = None
        if self.instance and hasattr(self.instance, 'user'):
            current_user = self.instance.user
        return _validate_unique_user_email(self.cleaned_data.get('email'), current_user)

    def save(self, commit=True):
        profile = super().save(commit=False)

        # Save related user fields
        user = profile.user
        user.username = self.cleaned_data['username']
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        user.email = self.cleaned_data['email']
        if commit:
            user.save(update_fields=['username', 'first_name', 'last_name', 'email'])
            profile.save()
        return profile









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
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)

        # Make required only for non-superuser creation
        for field_name in ['phone_number']:
            self.fields[field_name].required = True

        # If instance exists and has a user, populate user-related fields
        if self.instance and self.instance.pk and hasattr(self.instance, 'user'):
            user = self.instance.user
            self.fields['first_name'].initial = user.first_name
            self.fields['last_name'].initial = user.last_name
            self.fields['email'].initial = user.email

    def clean_email(self):
        current_user = None
        if self.instance and self.instance.pk and hasattr(self.instance, 'user'):
            current_user = self.instance.user
        return _validate_unique_user_email(self.cleaned_data.get('email'), current_user)

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
            # Use update_fields to avoid overwriting the password or other fields
            # edited concurrently (e.g. via password reset).
            user.save(update_fields=['username', 'first_name', 'last_name', 'email'])
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
            user.save(update_fields=['password'])

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
            full_reset = urljoin(self._resolve_base_url(), url)

            # From address
            site = SiteSettings.objects.first()
            # from_email = site.email if site and site.email else settings.DEFAULT_FROM_EMAIL
            from_email = settings.DEFAULT_FROM_EMAIL

            company_name = (
                site.company_name if site and site.company_name else "GGI"
            )
            site_domain = str(getattr(settings, "SITE_DOMAIN", "") or "").rstrip("/")
            invite_context = {
                "first_name": first_name,
                "company_name": company_name,
                "reset_link": full_reset,
                "site_domain": site_domain,
            }

            subject = render_to_string(
                "application/new_user_welcome_subject.txt",
                invite_context,
            ).strip().replace("\n", "")
            message = render_to_string(
                "application/new_user_welcome_email.txt",
                invite_context,
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

    def _resolve_base_url(self):
        """Resolve tenant-aware origin for invitation/reset links."""
        if self.request:
            return self.request.build_absolute_uri('/').rstrip('/')

        site_domain = str(getattr(settings, 'SITE_DOMAIN', '') or '').strip().rstrip('/')
        if site_domain:
            if '://' not in site_domain:
                site_domain = f"http://{site_domain}"
            return site_domain

        return 'http://localhost:8080'



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






