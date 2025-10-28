from datetime import datetime, timedelta

from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
import logging

from django.core.mail import EmailMultiAlternatives
from django.db.models import DecimalField, F, ExpressionWrapper, Case, When
from django.template.loader import render_to_string

from .models import TitleDeedCollection, ClientDoc, DocType, SubService, ClientSubService, SiteSettings, \
    SmsProviderToken, Document, Expense, ServiceCategory, Booking, BookingAssignment

from .models import Client, ClientService, Service, Process
from django.conf import settings
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm

from ..Employee.models import EmployeeProfile

logger = logging.getLogger(__name__)

# forms.py
from django import forms
from django.contrib.auth.forms import AuthenticationForm


# apps/EasyDocs/forms.py

from django import forms
from django.core.exceptions import ValidationError
import json

import json
from django import forms
from django.core.exceptions import ValidationError
from apps.EasyDocs.models import SiteSettings
from apps.EasyDocs.files.security import credential_service


class GoogleDriveConfigForm(forms.ModelForm):
    # Service account key upload
    service_account_key = forms.FileField(
        required=False,
        widget=forms.FileInput(attrs={
            'accept': '.json',
            'class': 'form-control',
        }),
        help_text="Upload your Google service account JSON key. Leave blank to keep the existing key."
    )

    # OAuth client secret (for adding/updating)
    google_oauth_client_secret = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter OAuth Client Secret'
        }),
        help_text="Leave blank to keep the existing secret."
    )

    class Meta:
        model = SiteSettings
        fields = [
            "google_drive_enabled",
            "google_drive_root_folder_id",
            "drive_auto_folder_creation",
            "drive_file_naming_pattern",
            "google_oauth_client_id",
        ]
        widgets = {
            "google_drive_enabled": forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            "google_drive_root_folder_id": forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter Google Drive Folder ID'
            }),
            "drive_auto_folder_creation": forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            "drive_file_naming_pattern": forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'E.g. {client_last_name}_{service_name}'
            }),
            "google_oauth_client_id": forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter OAuth Client ID'
            }),
        }

    def save(self, commit=True):
        """
        Save Google Drive settings, encrypting secrets as needed.
        - Service account key: encrypt and extract client_email
        - OAuth client secret: encrypt if provided
        """
        instance = super().save(commit=False)

        # --- Service account key ---
        key_file = self.cleaned_data.get("service_account_key")
        if key_file:
            content = key_file.read().decode("utf-8")
            # Encrypt the service account key and store email
            instance.google_drive_service_account_key_encrypted = credential_service.encrypt_service_account_key(content)
            instance.google_drive_service_account_email = json.loads(content).get("client_email")
            instance.drive_config_status = "configured"

        # --- OAuth client secret ---
        raw_secret = self.cleaned_data.get("google_oauth_client_secret")
        if raw_secret:
            instance.google_oauth_client_secret_encrypted = credential_service.encrypt(raw_secret)


        if commit:
            instance.save()

        return instance





class CustomAuthenticationForm(AuthenticationForm):
    username = forms.CharField(
        max_length=254,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your username',
            'required': 'required',
            'autofocus': 'autofocus',
        })
    )
    password = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your password',
            'required': 'required',
        }),
    )

    def clean_username(self):
        username = self.cleaned_data.get('username')
        if username:
            username = username.strip()
        return username


class CustomSetPasswordForm(SetPasswordForm):
    new_password1 = forms.CharField(
        label="New password",
        strip=False,
        widget=forms.PasswordInput(attrs={
            'autocomplete': 'new-password',
            'class': 'form-control',
            'placeholder': 'Enter new password',
        }),
        help_text='<ul>'
                  '<li>Your password can’t be too similar to your other personal information.</li>'
                  '<li>Your password must contain at least 8 characters.</li>'
                  '<li>Your password can’t be a commonly used password.</li>'
                  '<li>Your password can’t be entirely numeric.</li>'
                  '</ul>',
        validators=[validate_password],
    )

    new_password2 = forms.CharField(
        label="Confirm new password",
        strip=False,
        widget=forms.PasswordInput(attrs={
            'autocomplete': 'new-password',
            'class': 'form-control',
            'placeholder': 'Confirm new password',
        }),
    )

class CustomPasswordResetForm(PasswordResetForm):
    email = forms.EmailField(
        max_length=254,
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your email',
            'type': 'email',
        })
    )

    # Override this method in the form to make sure it's using your custom logic
    def save(self, domain_override=None,
             subject_template_name=None,
             email_template_name=None,
             use_https=False, token_generator=default_token_generator,
             from_email=None, request=None, html_email_template_name=None,
             extra_email_context=None):
        """
        Override the save method to ensure our custom send_mail gets called
        """
        print("CustomPasswordResetForm.save called")
        logger.debug("CustomPasswordResetForm.save called with email: %s", self.cleaned_data.get('email', 'unknown'))

        # Call the parent's save method which will eventually call send_mail
        return super().save(domain_override, subject_template_name,
                            email_template_name, use_https, token_generator,
                            from_email, request, html_email_template_name,
                            extra_email_context)

    def send_mail(
            self,
            subject_template_name,
            email_template_name,
            context,
            from_email,
            to_email,
            html_email_template_name=None,
    ):
        print("CustomPasswordResetForm.send_mail called")
        try:
            # Log backend and connection settings
            logger.debug("Email backend: %s", settings.EMAIL_BACKEND)
            logger.debug("Using SMTP server: %s:%s TLS=%s SSL=%s", settings.EMAIL_HOST, settings.EMAIL_PORT,
                         settings.EMAIL_USE_TLS, settings.EMAIL_USE_SSL)
            logger.debug("From: %s, To: %s", from_email, to_email)

            # Render subject & body
            subject = render_to_string(subject_template_name, context).strip().replace('\n', '')
            body = render_to_string(email_template_name, context)
            html_body = render_to_string(html_email_template_name, context) if html_email_template_name else None

            logger.debug("Email subject: %s", subject)
            logger.debug("Email body (text): %s", body)

            # Construct and send email
            email_message = EmailMultiAlternatives(subject, body, from_email, [to_email])
            if html_body:
                email_message.attach_alternative(html_body, 'text/html')

            result = email_message.send(fail_silently=False)
            logger.info("Email send result: %s", result)

        except Exception as e:
            logger.error("Exception occurred while sending password reset email: %s", str(e), exc_info=True)
            raise  # Re-raise to let Django know it failed








class DocumentForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = ['doc_name', 'doc_type', 'location', 'reference', 'doc_file']






class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ['first_name', 'last_name', 'email', 'phone']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter first name'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter last name'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'Enter your email'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter your phone'}),
        }

    def __init__(self, *args, **kwargs):
        super(ClientForm, self).__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})

    def clean_phone(self):
        phone = self.cleaned_data.get('phone')
        qs = Client.objects.filter(phone=phone)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("A client with this phone number already exists.")
        return phone


# forms.py


class ClientServiceForm(forms.ModelForm):
    category = forms.ChoiceField(
        choices=ServiceCategory.choices,
        required=False,
        label="Service Category",
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    service = forms.ModelChoiceField(
        queryset=Service.objects.none(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    scheduled_date = forms.DateTimeField(
        required=False,
        label="Scheduled Date (for Ground services)",
        widget=forms.DateTimeInput(attrs={
            'type': 'datetime-local',
            'class': 'form-control',
        })
    )

    dispatch_preview = forms.CharField(
        required=False,
        label="Dispatch Message",
        help_text="You can refine this before sending.",
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 3,
        })
    )

    class Meta:
        model = ClientService
        fields = [
            'client',
            'category',
            'service',
            'land_description',
            'scheduled_date',
            'dispatch_preview',
        ]
        widgets = {
            'client': forms.Select(attrs={'class': 'form-select'}),
            'land_description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 4,
                'placeholder': 'Enter a brief land description...',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Filter services by selected category if present
        if 'category' in self.data:
            self.fields['service'].queryset = Service.objects.filter(
                category=self.data.get('category')
            )
        else:
            self.fields['service'].queryset = Service.objects.all()
        
        

    def clean(self):
        cleaned = super().clean()
        category = cleaned.get('category')
        # If category is GROUND, enforce scheduled_date + dispatch_preview
        if category == ServiceCategory.GROUND:
            sd = cleaned.get('scheduled_date')
            msg = cleaned.get('dispatch_preview', '').strip()
            errors = {}
            if not sd:
                errors['scheduled_date'] = ValidationError(
                    "A scheduled date is required for ground services."
                )
            if not msg:
                errors['dispatch_preview'] = ValidationError(
                    "A dispatch message is required for ground services."
                )
            if errors:
                raise ValidationError(errors)
        return cleaned

    def save(self, commit=True):
        # Just save the ClientService record. Booking is handled in the view.
        return super().save(commit=commit)

# class ClientServiceForm(forms.ModelForm):
#     category = forms.ChoiceField(
#         choices=ServiceCategory.choices,
#         required=False,
#         label="Service Category",
#         widget=forms.Select(attrs={'class': 'form-select'})
#     )
#
#     service = forms.ModelChoiceField(
#         queryset=Service.objects.none(),
#         widget=forms.Select(attrs={'class': 'form-select'})
#     )
#
#     scheduled_date = forms.DateTimeField(
#         required=False,
#         label="Scheduled Date (for Ground services)",
#         widget=forms.DateTimeInput(attrs={
#             'type': 'datetime-local',
#             'class': 'form-control',
#         })
#     )
#
#     dispatch_preview = forms.CharField(
#         required=False,
#         label="Dispatch Message",
#         help_text="You can refine this before sending.",
#         widget=forms.Textarea(attrs={
#             'class': 'form-control',
#             'rows': 3,
#         })
#     )
#
#     class Meta:
#         model = ClientService
#         fields = [
#             'client',
#             'category',
#             'service',
#             'land_description',
#             'scheduled_date',
#             'dispatch_preview',
#         ]
#         widgets = {
#             'client': forms.Select(attrs={'class': 'form-select'}),
#             'land_description': forms.Textarea(attrs={
#                 'class': 'form-control',
#                 'rows': 4,
#                 'placeholder': 'Enter a brief land description...',
#             }),
#         }
#
#     def __init__(self, *args, **kwargs):
#         super().__init__(*args, **kwargs)
#
#         # Filter services by selected category if present
#         if 'category' in self.data:
#             self.fields['service'].queryset = Service.objects.filter(
#                 category=self.data.get('category')
#             )
#         else:
#             self.fields['service'].queryset = Service.objects.all()
#
#     def save(self, commit=True):
#         client_service = super().save(commit=False)
#         if commit:
#             client_service.save()
#             self.save_m2m()
#
#         # Create Booking for ground services if needed
#         if client_service.service.category == ServiceCategory.GROUND:
#             sd = self.cleaned_data.get('scheduled_date') or (datetime.now() + timedelta(days=1, hours=9))
#             msg = self.cleaned_data.get('dispatch_preview', '').strip()
#             booking = Booking.objects.create(
#                 client_service=client_service,
#                 scheduled_date=sd,
#                 dispatch_message=msg or ''
#             )
#             if not booking.dispatch_message:
#                 booking.dispatch_message = booking.generate_default_message()
#                 booking.save(update_fields=['dispatch_message'])
#         return client_service


class BookingForm(forms.ModelForm):
    class Meta:
        model = Booking
        fields = ['scheduled_date', 'dispatch_message']
        widgets = {
            'scheduled_date': forms.DateTimeInput(attrs={
                'type': 'datetime-local',
                'class': 'form-control'
            }),
            'dispatch_message': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Optional custom dispatch message...'
            }),
        }

    def save(self, commit=True):
        booking = super().save(commit=False)
        if not booking.dispatch_message:
            booking.dispatch_message = booking.generate_default_message()
        if commit:
            booking.save()
        return booking


# forms.py


class BookingManageForm(forms.ModelForm):
    surveyors = forms.ModelMultipleChoiceField(
        queryset=User.objects.filter(employeeprofile__role=EmployeeProfile.RoleChoices.SURVEYOR),
        required=False,
        widget=forms.CheckboxSelectMultiple
    )
    mark_handled = forms.BooleanField(
        required=False,
        label="Mark as handled"
    )

    class Meta:
        model = Booking
        fields = ['scheduled_date', 'dispatch_message']  # if you want inline reschedule/edit

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Pre‐populate surveyors field from the through‐model
        if self.instance.pk:
            self.fields['surveyors'].initial = self.instance.surveyors.values_list('pk', flat=True)
            self.fields['mark_handled'].initial = self.instance.handled

    def save(self, commit=True):
        booking = super().save(commit=False)
        # handle the boolean
        if self.cleaned_data['mark_handled'] and not booking.handled:
            booking.handled = True
            booking.handled_at = timezone.now()
            # handled_by will be set in the view
        elif not self.cleaned_data['mark_handled'] and booking.handled:
            booking.handled = False
            booking.handled_at = None
            booking.handled_by = None

        if commit:
            booking.save()
            # sync surveyors
            self.instance.bookingassignment_set.all().delete()
            for surveyor in self.cleaned_data['surveyors']:
                BookingAssignment.objects.create(
                    booking=booking,
                    surveyor=surveyor
                )
        return booking


class ServiceForm(forms.ModelForm):
    class Meta:
        model = Service
        fields = [
            'name',
            'description',
            'total_price',
            'category',

            'requires_title_collection',  # ← new field
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Service name'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'placeholder': 'Enter description', 'rows': 3}),
            'total_price': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'KSH'}),
            'category': forms.Select(attrs={'class': 'form-select'}),

            'requires_title_collection': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # never required by default; enforce only under TITLE category
        self.fields['requires_title_collection'].required = False

    def clean(self):
        cleaned = super().clean()
        cat = cleaned.get('category')

        needs_collection = cleaned.get('requires_title_collection')



        # new rule: TITLE services need the collection flag
        if cat == ServiceCategory.TITLE:
            if not needs_collection:
                self.add_error(
                    'requires_title_collection',
                    'Check this if the service requires a title‑deed collection step.'
                )
        else:
            # reset flag off for non‑title categories
            cleaned['requires_title_collection'] = False

        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)

        if instance.category != ServiceCategory.TITLE:
            instance.requires_title_collection = False

        if commit:
            instance.save()

        return instance


class ProcessForm(forms.ModelForm):
    class Meta:
        model = Process
        fields = ['name', 'description', 'step_order', 'cost', 'message']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter the name of the process'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'placeholder': 'Optional: Describe this process', 'rows': 3}),
            'step_order': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'E.g. 1, 2, 3...'}),
            'cost': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Enter the cost in KES', 'step': '0.01'}),
            'message': forms.Textarea(attrs={'class': 'form-control', 'placeholder': 'Message that will be sent to the client', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        self.service = kwargs.pop('service', None)
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        if Process.objects.filter(
            service=cleaned.get('service'),
            step_order=cleaned.get('step_order')
        ).exists():
            raise forms.ValidationError("This step order is already used for this service.")
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.service:
            instance.service = self.service
        if commit:
            instance.save()
        return instance

class TitleDeedCollectionForm(forms.ModelForm):
    class Meta:
        model = TitleDeedCollection
        fields = ['collected_by', 'id_number', 'phone_number', 'message']
        widgets = {
            'collected_by': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter name'}),
            'id_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter ID number'}),
            'phone_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter phone number'}),
            'message': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': 'Title deed collection confirmation',
                'rows': 3,
                # No explicit 'id' here, Django will generate 'id_message'
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields['message'].initial = ""  # Keep empty, JS will update dynamically


class ClientDocumentForm(forms.ModelForm):
    class Meta:
        model = ClientDoc
        fields = ['doc_name', 'doc_type', 'doc_file']
        widgets = {
            'doc_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter document name'
            }),
            'doc_type': forms.Select(attrs={
                'class': 'form-select'
            }),
            'doc_file': forms.ClearableFileInput(attrs={
                'class': 'form-control',
                'accept': '.pdf,.doc,.docx,.jpg,.png'  # Optional: file type filtering
            }),
        }
        labels = {
            'doc_name': 'Document Name',
            'doc_type': 'Document Type',
            'doc_file': 'Upload File',
        }

class DocTypeForm(forms.ModelForm):
    class Meta:
        model = DocType
        fields = ['name']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter new document type'
            })
        }


class SubServiceForm(forms.ModelForm):
    class Meta:
        model = SubService
        fields = ['name', 'department', 'description', 'price']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter lega service  name (e.g. Legal stamp)',
            }),
            'department': forms.Select(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 4,
                'placeholder': 'Provide a detailed description (optional)',
            }),
            'price': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter price (in KSH)',
                'min': 0,
            }),
        }

from django import forms
from django.db.models import F, Case, When, DecimalField, ExpressionWrapper
from .models import ClientSubService

class LegalPayoutForm(forms.Form):
    subservices = forms.ModelMultipleChoiceField(
        queryset=ClientSubService.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        label="Unpaid Sub‑services",
        help_text="Select all unpaid sub-services you want to pay out."
    )
    paid_month = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'month', 'class': 'form-control'}),
        label="Payout Month"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['subservices'].queryset = self.get_unpaid_subservices()

    def get_unpaid_subservices(self):
        return ClientSubService.objects.select_related('sub_service').annotate(
            annotated_price=Case(
                When(overridden_price__isnull=False, then=F('overridden_price')),
                default=F('sub_service__price'),
                output_field=DecimalField()
            ),
            annotated_balance=ExpressionWrapper(
                Case(
                    When(overridden_price__isnull=False, then=F('overridden_price')),
                    default=F('sub_service__price'),
                    output_field=DecimalField()
                ) - F('paid_amount'),
                output_field=DecimalField()
            )
        ).filter(annotated_balance__gt=0)

    def clean_paid_month(self):
        d = self.cleaned_data['paid_month']
        return d.replace(day=1)



class ClientSubServiceForm(forms.ModelForm):
    class Meta:
        model = ClientSubService
        fields = ['client_service', 'sub_service', 'overridden_price']
        widgets = {
            'client_service': forms.Select(attrs={'class': 'form-control'}),
            'sub_service': forms.Select(attrs={'class': 'form-control'}),
            'overridden_price': forms.NumberInput(attrs={'class': 'form-control'}),
        }

    def clean_overridden_price(self):
        price = self.cleaned_data.get("overridden_price")
        sub = self.cleaned_data.get("sub_service")

        if price:
            if price < sub.price:
                raise forms.ValidationError(
                    f"Cannot set overridden price below default ({sub.price})"
                )
        return price



class ClientSubServiceEditForm(forms.ModelForm):
    class Meta:
        model = ClientSubService
        fields = ['overridden_price']
        widgets = {
            'overridden_price': forms.NumberInput(attrs={'class': 'form-control'}),
        }

    def clean_overridden_price(self):
        price = self.cleaned_data.get('overridden_price')
        if price is not None and price < 0:
            raise forms.ValidationError("Price cannot be negative.")
        return price


class SiteSettingsForm(forms.ModelForm):
    class Meta:
        model = SiteSettings
        fields = ['company_name', 'company_phone','company_email','tagline', 'logo', 'stamp_signature']
        widgets = {
            'company_name': forms.TextInput(attrs={'class': 'form-control'}),

            'company_phone':        forms.TextInput(attrs={'class': 'form-control'}),
            'company_email':        forms.EmailInput(attrs={'class': 'form-control'}),
            'tagline':      forms.TextInput(attrs={'class': 'form-control'}),
            'logo':         forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'stamp_signature': forms.ClearableFileInput(attrs={'class': 'form-control'}),
        }


# forms.py


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ['description', 'amount', 'payment_mode', 'handled_by', 'approved_by', 'receipt_no']
        widgets = {
            'description': forms.TextInput(attrs={'class': 'form-control'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'payment_mode': forms.Select(attrs={'class': 'form-control'}),
            'handled_by': forms.Select(attrs={'class': 'form-control'}),
            'approved_by': forms.Select(attrs={'class': 'form-control'}),
            'receipt_no': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Restrict approved_by to users who are Admins
        self.fields['approved_by'].queryset = User.objects.filter(
            employeeprofile__role=EmployeeProfile.RoleChoices.ADMIN
        )
        
    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount <= 0:
            raise forms.ValidationError("Expense amount must be positive")

        from apps.accounts.models import CashbookEntry
        current_balance = CashbookEntry.current_balance()
        if current_balance < amount:
            raise forms.ValidationError(
                f"Insufficient funds. Current balance: {current_balance}."
            )
        return amount


class SmsProviderTokenForm(forms.ModelForm):
    class Meta:
        model = SmsProviderToken
        fields = ['api_token', 'sender_id']

class ClientSmsForm(forms.Form):
    message = forms.CharField(
        label="Message",
        widget=forms.Textarea(attrs={
            'placeholder': 'Type your message here...',
            'rows': 3,
            'class': 'form-control',
        }),
        max_length=480,  # Safely under 3 SMS parts
        required=True,
    )


# forms.py
# forms.py


from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.timezone import get_current_timezone



class BulkSmsForm(forms.Form):
    message = forms.CharField(
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 4,
            'placeholder': 'Type your message here...'
        }),
        label="Message Template",
        max_length=500
    )
    scheduled_date = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(
            attrs={'type': 'datetime-local', 'class': 'form-control'}
        ),
        label="Schedule for"
    )

    def clean(self):
        cleaned = super().clean()
        scheduled_date = cleaned.get('scheduled_date')


        if scheduled_date and scheduled_date <= timezone.now():
            self.add_error('scheduled_date', "Scheduled time must be in the future.")

