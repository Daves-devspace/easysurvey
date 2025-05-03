import logging

from django import forms
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode


from .models import EmployeeProfile
import secrets
import string

from ..EasyDocs.models import SiteSettings
from ..EasyDocs.utils import load_email_settings

logger = logging.getLogger(__name__)

def generate_random_password(length=8):
    # Define the characters to choose from
    alphabet = string.ascii_letters + string.digits + string.punctuation
    # Randomly choose characters from the alphabet
    return ''.join(secrets.choice(alphabet) for i in range(length))



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
        # Extract custom fields
        first_name = self.cleaned_data.pop('first_name')
        last_name  = self.cleaned_data.pop('last_name')
        email      = self.cleaned_data.pop('email')

        # Check if this is an update
        is_update = self.instance and self.instance.pk and hasattr(self.instance, 'user')

        if is_update:
            # Update existing user
            user = self.instance.user
            user.first_name = first_name
            user.last_name  = last_name
            user.email      = email
            user.username   = email.split('@')[0]
            user.save()
        else:
            # Create new user
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

        # Create or update EmployeeProfile
        employee_profile = super().save(commit=False)
        employee_profile.user = user

        if commit:
            employee_profile.save()

        # Send email only if new
        if not is_update:
            try:
                # Load and log email settings
                load_email_settings()
                logger.debug(
                    "Loaded email settings → host=%s port=%s user=%s ssl=%s tls=%s",
                    settings.EMAIL_HOST,
                    settings.EMAIL_PORT,
                    settings.EMAIL_HOST_USER,
                    settings.EMAIL_USE_SSL,
                    settings.EMAIL_USE_TLS,
                )

                # Build reset URL
                uid        = urlsafe_base64_encode(force_bytes(user.pk))
                token      = default_token_generator.make_token(user)
                reset_url  = reverse('password_reset_confirm', kwargs={'uidb64': uid, 'token': token})
                full_reset = f"{settings.SITE_DOMAIN}{reset_url}"

                # Determine from_email and company name
                site       = SiteSettings.objects.first()
                from_email = site.email if site and site.email else settings.DEFAULT_FROM_EMAIL
                company    = site.company_name if site else 'Company'

                # Prepare mail
                subject = "Your Account Credentials"
                message = (
                    f"Hello {first_name},\n\n"
                    f"Your account has been created.\n"
                    f"Username: {email}\n"
                    f"Temporary Password: {password}\n\n"
                    f"Please reset your password: {full_reset}\n\n"
                    f"Regards,\n{company}"
                )

                # Log right before sending
                logger.debug(
                    "About to send email → from=%s to=%s subject=%r",
                    from_email, [email], subject
                )

                send_mail(subject, message, from_email, [email])

            except Exception as e:
                logger.error(
                    "Failed to send email (host=%s port=%s ssl=%s tls=%s): %s",
                    settings.EMAIL_HOST, settings.EMAIL_PORT,
                    settings.EMAIL_USE_SSL, settings.EMAIL_USE_TLS,
                    e,
                    exc_info=True
                )

        return employee_profile


