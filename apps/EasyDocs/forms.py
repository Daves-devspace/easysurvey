from django import forms
from django.core.exceptions import ValidationError
from django.forms import TextInput
from .models import TitleDeedCollection

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

    def clean_step_order(self):
        step_order = self.cleaned_data.get('step_order')
        if self.service and Process.objects.filter(service=self.service, step_order=step_order).exclude(pk=self.instance.pk).exists():
            raise ValidationError(f"Step {step_order} already exists for this service.")
        return step_order

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
        fields = ['collected_by', 'id_number', 'phone_number']
        widgets = {
            'collected_by': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter name'}),
            'id_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter ID number'}),
            'phone_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter phone number'}),
        }
