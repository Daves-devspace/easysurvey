from datetime import datetime, timedelta

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
import logging

from django.core.mail import EmailMultiAlternatives
from django.db.models import DecimalField, F, ExpressionWrapper, Case, When
from django.template.loader import render_to_string

from .models import TitleDeedCollection, ClientDoc, DocType, SubService, ClientSubService, SiteSettings, \
    SmsProviderToken, Document, Expense, ServiceCategory, Booking

from .models import Client, ClientService, Service, Process
from django.conf import settings
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm

logger = logging.getLogger(__name__)


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
            logger.debug("Using SMTP server: %s:%s TLS=%s SSL=%s", settings.EMAIL_HOST, settings.EMAIL_PORT, settings.EMAIL_USE_TLS, settings.EMAIL_USE_SSL)
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
        fields = ['doc_name', 'doc_type', 'location', 'reference', 'file']






class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ['first_name','last_name', 'email', 'phone']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter your name'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'Enter your email'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter your phone'}),
        }

    def __init__(self, *args, **kwargs):
        super(ClientForm, self).__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})




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
            'collected_by': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter name', 'oninput': 'updateMessage()'}),
            'id_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter ID number', 'oninput': 'updateMessage()'}),
            'phone_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter phone number'}),
            'message': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': 'Title deed collection confirmation',
                'rows': 3,
                'id': 'message'
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields['message'].initial = (
                "Your title deed has been collected by {collected_by} (ID: {id_number})."
            )


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
        price = self.cleaned_data.get('overridden_price')
        if price is not None and price < 0:
            raise forms.ValidationError("Price cannot be negative.")
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
        fields = ['company_name', 'phone', 'tagline', 'logo', 'stamp_signature']
        widgets = {
            'company_name': forms.TextInput(attrs={'class': 'form-control'}),

            'phone':        forms.TextInput(attrs={'class': 'form-control'}),
            'tagline':      forms.TextInput(attrs={'class': 'form-control'}),
            'logo':         forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'stamp_signature': forms.ClearableFileInput(attrs={'class': 'form-control'}),
        }


# forms.py


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ['description','amount','payment_mode','handled_by','approved_by','receipt_no']
        widgets = {
            'description': forms.TextInput(attrs={'class':'form-control'}),
            'amount': forms.NumberInput(attrs={'class':'form-control', 'step':'0.01'}),
            'payment_mode': forms.Select(attrs={'class':'form-control'}),
            'handled_by': forms.Select(attrs={'class':'form-control'}),
            'approved_by': forms.Select(attrs={'class':'form-control'}),
            'receipt_no': forms.TextInput(attrs={'class':'form-control'}),
        }



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

PLACEHOLDERS = [
    ('{client_first_name}', 'Client First Name'),
    # ('{client_last_name}', 'Client Last Name'),  # add if desired
]

class BulkSmsForm(forms.Form):
    message = forms.CharField(widget=forms.Textarea(attrs={'class': 'form-control'}), label="Message Template")
    scheduled_time = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
        label="Send At (optional)"
    )
    recurring = forms.BooleanField(required=False, label="Repeat Monthly", widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))

    def clean_message(self):
        msg = self.cleaned_data['message']
        if '{client_first_name}' not in msg:
            raise forms.ValidationError("Your message must include the {client_first_name} placeholder.")
        return msg

    def clean(self):
        cleaned = super().clean()
        scheduled_time = cleaned.get('scheduled_time')
        recurring = cleaned.get('recurring')

        if scheduled_time and scheduled_time <= timezone.now():
            self.add_error('scheduled_time', "Scheduled time must be in the future.")

        if recurring and not scheduled_time:
            self.add_error('recurring', "You must select a send time for a recurring broadcast.")

