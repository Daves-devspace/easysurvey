from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
import logging
from .models import TitleDeedCollection, ClientDoc, DocType, SubService, ClientSubService, SiteSettings, \
    SmsProviderToken, EmailSettings, Document, Expense, ServiceCategory

from .models import Client, ClientService, Service, Process
from django.conf import settings
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm
from .utils import load_email_settings
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
        # 1) load your DB settings (host/port/user/pass + SSL/TLS flags)
        load_email_settings()
        logger.debug(
            "Password reset email using host=%s port=%s ssl=%s tls=%s",
            settings.EMAIL_HOST,
            settings.EMAIL_PORT,
            settings.EMAIL_USE_SSL,
            settings.EMAIL_USE_TLS,
        )

        # 2) delegate to the built‑in implementation
        return super().send_mail(
            subject_template_name,
            email_template_name,
            context,
            from_email,
            to_email,
            html_email_template_name=html_email_template_name,
        )








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




class ClientServiceForm(forms.ModelForm):
    category = forms.ChoiceField(
        choices=ServiceCategory.choices,
        required=False,
        label="Service Category",
        widget=forms.Select(attrs={
            'class': 'form-select',
        })
    )

    service = forms.ModelChoiceField(
        queryset=Service.objects.none(),
        widget=forms.Select(attrs={
            'class': 'form-select',
        })
    )

    dispatch_preview = forms.CharField(
        required=False,
        label="Dispatch Message",
        help_text="Preview only for dispatch services",
        widget=forms.Textarea(attrs={
            'readonly': True,
            'class': 'form-control',
            'rows': 3,
        })
    )

    class Meta:
        model = ClientService
        fields = ['client', 'category', 'service', 'land_description']
        widgets = {
            'client': forms.Select(attrs={
                'class': 'form-select',
            }),
            'land_description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 4,
                'placeholder': 'Enter a brief land description...',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Filter the services if category is provided via POST
        if 'category' in self.data:
            category = self.data.get('category')
            self.fields['service'].queryset = Service.objects.filter(category=category)
        else:
            self.fields['service'].queryset = Service.objects.all()

        # Hide dispatch preview unless dispatch service is selected
        if 'service' in self.data:
            try:
                service_id = int(self.data.get('service'))
                service = Service.objects.get(id=service_id)
                if service.category == ServiceCategory.GROUND and service.dispatch_message:
                    self.fields['dispatch_preview'].initial = service.dispatch_message
                else:
                    self.fields['dispatch_preview'].widget = forms.HiddenInput()
            except (ValueError, Service.DoesNotExist):
                self.fields['dispatch_preview'].widget = forms.HiddenInput()
        else:
            self.fields['dispatch_preview'].widget = forms.HiddenInput()





class ServiceForm(forms.ModelForm):
    class Meta:
        model = Service
        # add dispatch_message here
        fields = ['name', 'description', 'total_price', 'category', 'dispatch_message']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Service name'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'placeholder': 'Enter description','rows': 3}),
            'total_price': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'KSH'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'dispatch_message': forms.Textarea(attrs={'class': 'form-control', 'placeholder': 'Dispatch SMS content'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # never required by default; we'll enforce it only if category=GROUND
        self.fields['dispatch_message'].required = False

    def clean(self):
        cleaned = super().clean()
        cat = cleaned.get('category')
        dispatch = cleaned.get('dispatch_message')

        if cat == ServiceCategory.GROUND and not dispatch:
            self.add_error(
                'dispatch_message',
                'This message is required for dispatch-based (GROUND) services.'
            )
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
                'placeholder': 'Enter SubService name',
            }),
            'department': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter department name (e.g. Legal Department)',
            }),
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

class EmailSettingsForm(forms.ModelForm):
    email_host_password = forms.CharField(
        required=False,
        label="Email Host Password",
        help_text="Use at least 8 characters, mixing letters & numbers.",
        widget=forms.PasswordInput(
            render_value=True,
            attrs={
                'class': 'form-control',
                'placeholder': 'Enter email password',
                'id': 'id_email_host_password',
            }
        )
    )

    class Meta:
        model = EmailSettings
        fields = [
            'email_host',
            'email_port',
            'email_host_user',
            'email_host_password',
            'default_from_email'
        ]
        widgets = {
            'email_host': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter email host'
            }),
            'email_port': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter email port'
            }),
            'email_host_user': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter email user'
            }),
            'default_from_email': forms.EmailInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter default from email (e.g. admin@valuetech.co.ke)'
            }),
        }





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

