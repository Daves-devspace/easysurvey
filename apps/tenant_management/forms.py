# forms.py
from django import forms
from .models import Unit, Property, Lease, MeterReading
from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import Tenant, Unit, Lease, Property, MeterReading, WaterCompany,Payment

from datetime import date as dt_date

class UnitForm(forms.ModelForm):
    class Meta:
        model = Unit
        fields = ['unit_number', 'rent_amount', 'meter_number']
        widgets = {
            'unit_number': forms.TextInput(attrs={'class': 'form-control'}),
            'rent_amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'meter_number': forms.TextInput(attrs={'class': 'form-control'}),
        }


class PropertyForm(forms.ModelForm):
    class Meta:
        model = Property
        fields = ['name', 'location', 'water_policy', 'water_company']

        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter property name',
            }),
            'location': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter property location',
            }),
            'water_policy': forms.Select(attrs={
                'class': 'form-select',
            }),
            'water_company': forms.Select(attrs={
                'class': 'form-select',
            }),
        }

        help_texts = {
            'name': 'The name used to identify the property.',
            'location': 'Describe the physical address or location.',
            'water_policy': 'Choose how water billing should be handled: shared, metered, or prepaid.',
            'water_company': 'Select the water company that supplies this property.',
        }

    def clean(self):
        """
        Custom validation.
        If water_policy is 'meter', ensure the selected water company has an active rate.
        """
        cleaned_data = super().clean()
        policy = cleaned_data.get('water_policy')
        company = cleaned_data.get('water_company')

        if policy == Property.METER and company:
            active_rate = company.water_rates.filter(is_active=True).first()
            if not active_rate:
                self.add_error(
                    'water_company',
                    f"{company.name} does not have an active water rate. Please add one before assigning."
                )

        return cleaned_data
    
    
    
class LeaseForm(forms.ModelForm):
    class Meta:
        model = Lease
        fields = ['tenant', 'start_date', 'deposit_amount']
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
        }
        
        





class TenantCreationForm(forms.ModelForm):
    class Meta:
        model = Tenant
        fields = ['full_name', 'phone_number', 'email', 'national_id']
        widgets = {
            'full_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter full name', 'required': True}),
            'phone_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '+254712345678', 'pattern': r'^\+?254[0-9]{9}$', 'title': 'Enter valid Kenyan phone number'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'tenant@example.com (optional)'}),
            'national_id': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter national ID number', 'maxlength': '20'}),
        }

    def __init__(self, *args, **kwargs):
        self.property = kwargs.pop('property', None)
        super().__init__(*args, **kwargs)

    def clean_phone_number(self):
        phone = self.cleaned_data.get('phone_number')
        if not phone:
            return phone

        # Normalize phone number
        phone = phone.replace(' ', '').replace('-', '')
        if phone.startswith('0'):
            phone = '+254' + phone[1:]
        elif not phone.startswith('+254'):
            phone = '+254' + phone.lstrip('+')

        if not phone.startswith('+254') or len(phone) != 13:
            raise ValidationError("Please enter a valid Kenyan phone number")

        return phone

    def clean_national_id(self):
        nid = self.cleaned_data.get('national_id')
        if nid:
            nid = ''.join(filter(str.isdigit, nid))
            if not (7 <= len(nid) <= 8):
                raise ValidationError("National ID must be 7-8 digits")
        return nid


class LeaseCreationForm(forms.ModelForm):
    """
    Form for creating a Lease. Dynamically limits unit choices to vacant units of a property.
    """
    # Override unit field to set queryset later in __init__
    unit = forms.ModelChoiceField(
        queryset=Unit.objects.none(),
        widget=forms.Select(attrs={'class': 'form-control'}),
        help_text="Select an available unit"
    )

    class Meta:
        model = Lease
        fields = ['unit', 'start_date', 'deposit_amount']
        widgets = {
            # Date picker that prevents past dates
            'start_date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date',
                'min': timezone.now().date().isoformat()
            }),
            # Numeric input for deposit
            'deposit_amount': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.01',
                'min': '0'
            })
        }

    def __init__(self, property_id=None, *args, **kwargs):
        """
        If property_id is passed, limit unit choices to that property's vacant units.
        """
        super().__init__(*args, **kwargs)
        if property_id:
            self.fields['unit'].queryset = Unit.objects.filter(
                property_id=property_id,
                is_occupied=False
            ).select_related('property')

    def clean_start_date(self):
        """
        Prevent start date in the past.
        """
        start_date = self.cleaned_data.get('start_date')
        if start_date and start_date < timezone.now().date():
            raise ValidationError("Start date cannot be in the past")
        return start_date

    def clean_deposit_amount(self):
        """
        Enforce deposit between 50% and 300% of monthly rent.
        """
        deposit = self.cleaned_data.get('deposit_amount')
        unit = self.cleaned_data.get('unit')
        if deposit and unit:
            min_dep = unit.rent_amount * 0.5
            max_dep = unit.rent_amount * 3
            if deposit < min_dep:
                raise ValidationError("Deposit is typically at least 50% of monthly rent")
            if deposit > max_dep:
                raise ValidationError("Deposit seems unusually high. Please verify.")
        return deposit



from decimal import Decimal


class CombinedTenantLeaseForm(forms.Form):
    # Tenant fields
    full_name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Full name'})
    )
    phone_number = forms.CharField(
        max_length=15,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '+2547XXXXXXXX'})
    )
    email = forms.EmailField(
        required=False, 
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'Email (optional)'})
    )
    national_id = forms.CharField(
        max_length=20, 
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'National ID'})
    )

    # Hidden authoritative IDs (integers) - view sets initial; JS sets unit on click
    property = forms.IntegerField(widget=forms.HiddenInput())
    unit = forms.IntegerField(widget=forms.HiddenInput())

    # Lease fields
    start_date = forms.DateField(
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    deposit_amount = forms.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), 
        required=False, 
        initial=Decimal('0.00')
    )
    
    # --- NEW FIELD: Initial Reading ---
    initial_reading = forms.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': 'e.g. 1050.00'}),
        required=True, 
        label="Initial Meter Reading",
        help_text="The number currently on the water meter."
    )

    def clean_phone_number(self):
        phone = self.cleaned_data.get('phone_number')
        if phone:
            phone = phone.replace(' ', '').replace('-', '')
            if phone.startswith('0'):
                phone = '+254' + phone[1:]
            if Tenant.objects.filter(phone_number=phone).exists():
                raise ValidationError("A tenant with this phone already exists.")
        return phone

    def clean_national_id(self):
        nid = self.cleaned_data.get('national_id')
        if nid:
            nid = ''.join(filter(str.isdigit, nid))
            if Tenant.objects.filter(national_id=nid).exists():
                raise ValidationError("A tenant with this national ID already exists.")
        return nid

    def clean(self):
        cleaned = super().clean()
        prop_id = cleaned.get('property')
        unit_id = cleaned.get('unit')

        if prop_id is None or not Property.objects.filter(pk=prop_id).exists():
            raise ValidationError("Invalid property selected.")

        if unit_id is None:
            raise ValidationError("Unit is required.")
        try:
            unit = Unit.objects.get(pk=unit_id)
        except Unit.DoesNotExist:
            raise ValidationError("Invalid unit selected.")

        if unit.property_id != prop_id:
            raise ValidationError("Selected unit does not belong to the chosen property.")

        if unit.is_occupied:
            raise ValidationError("Selected unit is already occupied.")

        return cleaned




class BillingPeriodMixin(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "billing_period" not in self.fields:
            self.fields["billing_period"] = forms.DateField(
                required=True,
                widget=forms.DateInput(attrs={"type": "month"}),
                input_formats=["%Y-%m"],  # <-- Accept "YYYY-MM" from type="month"
                help_text="Billing month this reading belongs to",
            )

        if not self.initial.get("billing_period"):
            today = timezone.now().date()
            if today.month == 1:
                year, month = today.year - 1, 12
            else:
                year, month = today.year, today.month - 1
            self.initial["billing_period"] = dt_date(year, month, 1)

    def clean_billing_period(self):
        val = self.cleaned_data["billing_period"]
        # Ensure day is always 1
        return val.replace(day=1)



class MeterReadingCreateForm(BillingPeriodMixin, forms.ModelForm):
    class Meta:
        model = MeterReading
        fields = [ "previous_reading"]
        widgets = {
            "previous_reading": forms.NumberInput(attrs={"step": "0.01", "min": "0"})
        }

    def clean_previous_reading(self):
        v = self.cleaned_data.get("previous_reading")
        if v is None:
            raise forms.ValidationError("Previous reading is required.")
        if v < 0:
            raise forms.ValidationError("Reading cannot be negative.")
        return v


class MeterReadingUpdateForm(BillingPeriodMixin, forms.ModelForm):
    class Meta:
        model = MeterReading
        fields = ["current_reading"]
        widgets = {
            "current_reading": forms.NumberInput(attrs={"step": "0.01", "min": "0"})
        }

    def clean_current_reading(self):
        v = self.cleaned_data.get("current_reading")
        if v is None:
            raise forms.ValidationError("Current reading is required.")
        if v < 0:
            raise forms.ValidationError("Reading cannot be negative.")
        return v
    
    
class PaymentForm(forms.Form):
    amount = forms.DecimalField(
        max_digits=12, decimal_places=2,
        min_value=0.01,
        widget=forms.NumberInput(attrs={"step": "0.01", "class": "form-control", "placeholder": "Amount"})
    )
    invoice_id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    method = forms.CharField(
        max_length=50, required=True, initial="Mpesa",
        widget=forms.TextInput(attrs={"class": "form-control"})
    )
    reference = forms.CharField(
        max_length=100, required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Payment reference (optional)"})
    )

    def clean_amount(self):
        val = self.cleaned_data["amount"]
        # Django DecimalField already validates range and scale. Extra checks can go here.
        if val <= 0:
            raise forms.ValidationError("Amount must be greater than 0")
        return val