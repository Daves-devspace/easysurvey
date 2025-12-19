from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from decimal import Decimal
from datetime import date as dt_date
from .models import Tenant, Unit, Lease, Property, MeterReading, WaterCompany, Payment, WaterRate

# ... [Keep WaterCompanyForm, WaterRateForm, UnitForm, PropertyForm unchanged] ...
import logging
from apps.tenant_management.models import Tenant
from apps.tenant_management.comm.mobile_sasa import MobileSasaAPI

logger = logging.getLogger(__name__)

class AnnouncementForm(forms.Form):
    message = forms.CharField(
        widget=forms.Textarea(attrs={
            'class': 'form-control', 
            'rows': 4, 
            'placeholder': 'Type your announcement here...',
            'id': 'announcementMessage'
        }),
        help_text="This message will be sent to all active tenants in this property."
    )
    
    def clean_message(self):
        msg = self.cleaned_data.get('message')
        if not msg:
            raise ValidationError("Message cannot be empty.")
        return msg

class BulkCommunicationForm(forms.Form):
    MODE_NEW = 'new'
    MODE_REMINDER = 'reminder'
    MODE_CHOICES = [
        (MODE_NEW, "Send New Invoices (Not sent yet)"),
        (MODE_REMINDER, "Send Reminders (Unpaid Balances)")
    ]
    
    target_month = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'month', 'class': 'form-control'}, format='%Y-%m'),
        # FIX: Allow 'YYYY-MM' input format for month picker
        input_formats=['%Y-%m'], 
        label="Billing Month"
    )
    mode = forms.ChoiceField(
        choices=MODE_CHOICES, 
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Action"
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # FIX: Initialize with YYYY-MM string, not date object
        if 'target_month' not in self.initial:
             self.initial['target_month'] = timezone.now().strftime('%Y-%m')
    
    def clean_target_month(self):
        val = self.cleaned_data['target_month']
        # Normalize to 1st of month
        return val.replace(day=1)
class WaterCompanyForm(forms.ModelForm):
    class Meta:
        model = WaterCompany
        fields = ['name', 'contact_info']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Nairobi Water'}),
            'contact_info': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Address, Phone, Paybill...'}),
        }

class WaterRateForm(forms.ModelForm):
    class Meta:
        model = WaterRate
        fields = ['water_company', 'rate_per_cubic_meter', 'effective_from', 'effective_to', 'is_active']
        widgets = {
            'water_company': forms.Select(attrs={'class': 'form-select'}),
            'rate_per_cubic_meter': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'effective_from': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'effective_to': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
    def clean(self):
        cleaned_data = super().clean()
        is_active = cleaned_data.get('is_active')
        company = cleaned_data.get('water_company')
        if is_active and company:
            qs = WaterRate.objects.filter(water_company=company, is_active=True)
            if self.instance.pk: qs = qs.exclude(pk=self.instance.pk)
            if qs.exists(): self.add_error('is_active', "This company already has an active rate. Please deactivate the old one first.")
        return cleaned_data

class UnitForm(forms.ModelForm):
    class Meta:
        model = Unit
        fields = ['unit_number', 'rent_amount', 'meter_number']
        widgets = {
            'unit_number': forms.TextInput(attrs={'class': 'form-control'}),
            'rent_amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'meter_number': forms.TextInput(attrs={'class': 'form-control'}),
        }
    def __init__(self, *args, property_obj=None, **kwargs):
        super().__init__(*args, **kwargs)
        if property_obj and property_obj.water_policy == Property.PREPAID:
            if 'meter_number' in self.fields: del self.fields['meter_number']

class PropertyForm(forms.ModelForm):
    class Meta:
        model = Property
        fields = ['name', 'location', 'water_policy', 'water_company', 'billing_day']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter property name'}),
            'location': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter property location'}),
            'water_policy': forms.Select(attrs={'class': 'form-select'}),
            'water_company': forms.Select(attrs={'class': 'form-select'}),
            'billing_day': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'max': 31}),
        }
    def clean(self):
        cleaned_data = super().clean()
        policy = cleaned_data.get('water_policy')
        company = cleaned_data.get('water_company')
        if policy == Property.METER and company:
            active_rate = company.water_rates.filter(is_active=True).first()
            if not active_rate: self.add_error('water_company', f"{company.name} does not have an active water rate. Please add one before assigning.")
        return cleaned_data

class LeaseForm(forms.ModelForm):
    class Meta:
        model = Lease
        fields = ['tenant', 'start_date', 'deposit_amount']
        widgets = { 'start_date': forms.DateInput(attrs={'type': 'date'}), }

class TenantCreationForm(forms.ModelForm):
    class Meta:
        model = Tenant
        fields = ['full_name', 'phone_number', 'email', 'national_id']
        widgets = {
            'full_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter full name', 'required': True}),
            'phone_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '+254712345678'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'tenant@example.com (optional)'}),
            'national_id': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'National ID'}),
        }
    def clean_phone_number(self):
        phone = self.cleaned_data.get('phone_number')
        if phone:
            phone = phone.replace(' ', '').replace('-', '')
            if phone.startswith('0'): phone = '+254' + phone[1:]
        return phone
    def clean_national_id(self):
        nid = self.cleaned_data.get('national_id')
        if nid: nid = ''.join(filter(str.isdigit, nid))
        return nid

class CombinedTenantLeaseForm(forms.Form):
    full_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Full name'}))
    phone_number = forms.CharField(max_length=15, widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '+2547XXXXXXXX'}))
    email = forms.EmailField(required=False, widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'Email (optional)'}))
    national_id = forms.CharField(max_length=20, widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'National ID'}))
    property = forms.IntegerField(widget=forms.HiddenInput())
    unit = forms.IntegerField(widget=forms.HiddenInput())
    start_date = forms.DateField(widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}))
    deposit_amount = forms.DecimalField(max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), required=False, initial=Decimal('0.00'))
    
    initial_reading = forms.DecimalField(
        max_digits=10, decimal_places=2, 
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': 'e.g. 1050.00'}), 
        required=True, label="Initial Meter Reading", help_text="The number currently on the water meter."
    )

    def __init__(self, *args, property_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        if property_id:
            try:
                prop = Property.objects.get(pk=property_id)
                if prop.water_policy == Property.PREPAID:
                    if 'initial_reading' in self.fields: del self.fields['initial_reading']
            except Property.DoesNotExist: pass
    
    # ... [clean methods same as before] ...
    def clean_phone_number(self):
        phone = self.cleaned_data.get('phone_number')
        if phone:
            phone = phone.replace(' ', '').replace('-', '')
            if phone.startswith('0'): phone = '+254' + phone[1:]
        return phone
    def clean_national_id(self):
        nid = self.cleaned_data.get('national_id')
        if nid: nid = ''.join(filter(str.isdigit, nid))
        return nid
    def clean(self):
        cleaned = super().clean()
        prop_id = cleaned.get('property')
        unit_id = cleaned.get('unit')
        phone = cleaned.get('phone_number')
        nid = cleaned.get('national_id')
        if prop_id is None or not Property.objects.filter(pk=prop_id).exists(): raise ValidationError("Invalid property selected.")
        if unit_id is None: raise ValidationError("Unit is required.")
        try: unit = Unit.objects.get(pk=unit_id)
        except Unit.DoesNotExist: raise ValidationError("Invalid unit selected.")
        if unit.property_id != prop_id: raise ValidationError("Selected unit does not belong to the chosen property.")
        if unit.is_occupied: raise ValidationError("Selected unit is already occupied.")
        if prop_id and phone:
            if Tenant.objects.filter(property_id=prop_id, phone_number=phone).exists(): self.add_error('phone_number', "A tenant with this phone already exists in this property.")
        if prop_id and nid:
            if Tenant.objects.filter(property_id=prop_id, national_id=nid).exists(): self.add_error('national_id', "A tenant with this National ID already exists in this property.")
        return cleaned

class LeaseCreationForm(forms.ModelForm):
    # Field definition is crucial here so it exists before __init__ runs
    initial_reading = forms.DecimalField(
        max_digits=10, decimal_places=2, 
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), 
        required=True, label="Initial Meter Reading", help_text="Baseline reading for the new unit."
    )
    
    unit = forms.ModelChoiceField(queryset=Unit.objects.none(), widget=forms.Select(attrs={'class': 'form-control'}), help_text="Select an available unit")
    
    class Meta:
        model = Lease
        fields = ['unit', 'start_date', 'deposit_amount']
        widgets = {
            'start_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date', 'min': timezone.now().date().isoformat()}),
            'deposit_amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'})
        }

    def __init__(self, property_id=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if property_id: 
            self.fields['unit'].queryset = Unit.objects.filter(property_id=property_id, is_occupied=False).select_related('property')
            
            try:
                prop = Property.objects.get(pk=property_id)
                if prop.water_policy == Property.PREPAID:
                    # Robust removal
                    if 'initial_reading' in self.fields:
                        del self.fields['initial_reading']
            except Property.DoesNotExist:
                pass

    def clean_start_date(self):
        start_date = self.cleaned_data.get('start_date')
        if start_date and start_date < timezone.now().date(): raise ValidationError("Start date cannot be in the past")
        return start_date
    def clean_deposit_amount(self):
        deposit = self.cleaned_data.get('deposit_amount')
        unit = self.cleaned_data.get('unit')
        if deposit is not None and unit:
            min_dep = unit.rent_amount * Decimal("0.5")
            max_dep = unit.rent_amount * Decimal("3.0")
            if deposit < min_dep: raise ValidationError(f"Deposit is typically at least 50% of monthly rent (Min: {min_dep})")
            if deposit > max_dep: raise ValidationError("Deposit seems unusually high. Please verify.")
        return deposit

# ... [BillingPeriodMixin, MeterReadingCreateForm, MeterReadingUpdateForm, PaymentForm unchanged] ...
class BillingPeriodMixin(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "billing_period" not in self.fields:
            self.fields["billing_period"] = forms.DateField(required=True, widget=forms.DateInput(attrs={"type": "month"}), input_formats=["%Y-%m"], help_text="Billing month this reading belongs to")
        if not self.initial.get("billing_period"):
            today = timezone.now().date()
            if today.month == 1: year, month = today.year - 1, 12
            else: year, month = today.year, today.month - 1
            self.initial["billing_period"] = dt_date(year, month, 1)
    def clean_billing_period(self):
        val = self.cleaned_data["billing_period"]
        return val.replace(day=1)

class MeterReadingCreateForm(BillingPeriodMixin, forms.ModelForm):
    class Meta:
        model = MeterReading
        fields = ["previous_reading"]
        widgets = { "previous_reading": forms.NumberInput(attrs={"step": "0.01", "min": "0"}) }
    def clean_previous_reading(self):
        v = self.cleaned_data.get("previous_reading")
        if v is None: raise forms.ValidationError("Previous reading is required.")
        if v < 0: raise forms.ValidationError("Reading cannot be negative.")
        return v

class MeterReadingUpdateForm(BillingPeriodMixin, forms.ModelForm):
    class Meta:
        model = MeterReading
        fields = ["previous_reading", "current_reading"] 
        widgets = {
            "previous_reading": forms.NumberInput(attrs={"step": "0.01", "min": "0", "readonly": "readonly", "class": "form-control bg-light"}),
            "current_reading": forms.NumberInput(attrs={"step": "0.01", "min": "0"})
        }
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['previous_reading'].required = False
    def clean_current_reading(self):
        v = self.cleaned_data.get("current_reading")
        if v is None: raise forms.ValidationError("Current reading is required.")
        if v < 0: raise forms.ValidationError("Reading cannot be negative.")
        return v
    
class PaymentForm(forms.Form):
    amount = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0.01, widget=forms.NumberInput(attrs={"step": "0.01", "class": "form-control", "placeholder": "Amount"}))
    invoice_id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    method = forms.CharField(max_length=50, required=True, initial="Mpesa", widget=forms.TextInput(attrs={"class": "form-control"}))
    reference = forms.CharField(max_length=100, required=False, widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Payment reference (optional)"}))
    def clean_amount(self):
        val = self.cleaned_data["amount"]
        if val <= 0: raise forms.ValidationError("Amount must be greater than 0")
        return val