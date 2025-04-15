from django import forms
from django.core.exceptions import ValidationError
from django.forms import TextInput
from .models import TitleDeedCollection, ClientDoc, DocType, SubService, ClientSubService, SiteSettings, \
    SmsProviderToken, EmailSettings

from .models import Client, ClientService, Service, Process


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
    class Meta:
        model = ClientService
        fields = ['client', 'service','land_description']
        widgets = {
            'client': forms.Select(attrs={'class': 'form-control'}),
            'service': forms.Select(attrs={'class': 'form-control'}),
            'land_description': forms.TextInput(attrs={'class': 'form-control'}),
        }




class ServiceForm(forms.ModelForm):
    class Meta:
        model = Service
        fields = ['name', 'description', 'total_price']
        widgets = {
            'name':forms.TextInput(attrs={'class':'form-control','placeholder':'Service name'}),
            'description':forms.TextInput(attrs={'class':'form-control','placeholder':'Enter description'}),
            'total_price':forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': 'KSH'}),
        }



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
        fields = ['client_service', 'sub_service']
        widgets = {
            'client_service': forms.Select(attrs={'class': 'form-control'}),
            'sub_service': forms.Select(attrs={'class': 'form-control'}),
        }


class SiteSettingsForm(forms.ModelForm):
    class Meta:
        model = SiteSettings
        fields = ['company_name', 'email', 'phone', 'tagline', 'logo', 'stamp_signature']
        widgets = {
            'company_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email':        forms.EmailInput(attrs={'class': 'form-control'}),
            'phone':        forms.TextInput(attrs={'class': 'form-control'}),
            'tagline':      forms.TextInput(attrs={'class': 'form-control'}),
            'logo':         forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'stamp_signature': forms.ClearableFileInput(attrs={'class': 'form-control'}),
        }


# forms.py

class EmailSettingsForm(forms.ModelForm):
    email_host_password = forms.CharField(
        widget=forms.PasswordInput(render_value=True),
        required=False,
        label="Email Host Password"
    )

    class Meta:
        model = EmailSettings
        fields = ['email_host', 'email_port', 'email_host_user', 'email_host_password', 'default_from_email']

        widgets = {
            'email_host': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter email host'}),
            'email_port': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Enter email port'}),
            'email_host_user': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter email user'}),
            'default_from_email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'Enter default from email'}),
        }


class SmsProviderTokenForm(forms.ModelForm):
    class Meta:
        model = SmsProviderToken
        fields = ['api_token', 'sender_id']