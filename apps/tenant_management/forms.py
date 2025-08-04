# forms.py
from django import forms
from .models import Unit, Property, Lease


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
        fields = ['name', 'location', 'water_policy', 'water_rate']

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
            'water_rate': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.01',
                'placeholder': 'Ksh per cubic meter (optional)',
            }),
        }

        help_texts = {
            'name': 'The name used to identify the property.',
            'location': 'Describe the physical address or location.',
            'water_policy': 'Choose how water billing should be handled.',
            'water_rate': 'Applicable if water billing is per meter. Enter cost per cubic meter.',
        }

    def clean(self):
        """Custom validation to ensure water_rate is required when water_policy is 'meter'."""
        cleaned_data = super().clean()
        policy = cleaned_data.get('water_policy')
        rate = cleaned_data.get('water_rate')

        if policy == Property.METER and (rate is None or rate <= 0):
            self.add_error('water_rate', 'Water rate is required for metered properties.')

        return cleaned_data
    
    
    
class LeaseForm(forms.ModelForm):
    class Meta:
        model = Lease
        fields = ['tenant', 'start_date', 'deposit_amount']
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
        }
        
        



from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import Tenant, Unit, Lease, Property


class TenantCreationForm(forms.ModelForm):
    """
    Form for creating a new Tenant with normalization and validation.
    """
    class Meta:
        model = Tenant
        fields = ['full_name', 'phone_number', 'email', 'national_id']
        widgets = {
            # Style and placeholder for full_name field
            'full_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter full name',
                'required': True
            }),
            # Kenyan phone number format enforcement
            'phone_number': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '+254712345678',
                'pattern': r'^\+?254[0-9]{9}$',
                'title': 'Enter valid Kenyan phone number'
            }),
            # Optional email
            'email': forms.EmailInput(attrs={
                'class': 'form-control',
                'placeholder': 'tenant@example.com (optional)'
            }),
            # National ID: numeric only
            'national_id': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter national ID number',
                'maxlength': '20'
            })
        }

    def clean_phone_number(self):
        """
        Normalize and validate Kenyan phone formats:
        - Remove spaces/hyphens
        - Ensure +254 country code
        - Total length must be 13 (+254 and 9 digits)
        """
        phone = self.cleaned_data.get('phone_number')
        if phone:
            phone = phone.replace(' ', '').replace('-', '')
            # If starts with 0, convert to +254
            if phone.startswith('0'):
                phone = '+254' + phone[1:]
            elif not phone.startswith('+254'):
                # Prepend missing +254 if absent
                phone = '+254' + phone.lstrip('+')

            # Final format check
            if not phone.startswith('+254') or len(phone) != 13:
                raise ValidationError("Please enter a valid Kenyan phone number")
        return phone

    def clean_national_id(self):
        """
        Ensure national ID is 7-8 digits, strip non-numeric.
        """
        nid = self.cleaned_data.get('national_id')
        if nid:
            # Filter digits only
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


class CombinedTenantLeaseForm(forms.Form):
    """
    Single form to create a Tenant and Lease in one step.
    Dynamically loads units via AJAX when property changes.
    """
    # Tenant fields
    full_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class':'form-control','placeholder':'Full name'}))
    phone_number = forms.CharField(max_length=15, widget=forms.TextInput(attrs={'class':'form-control','placeholder':'+2547XXXXXXXX'}))
    email = forms.EmailField(required=False, widget=forms.EmailInput(attrs={'class':'form-control','placeholder':'Email (optional)'}))
    national_id = forms.CharField(max_length=20, widget=forms.TextInput(attrs={'class':'form-control','placeholder':'National ID'}))

    # Lease-related fields
    property = forms.ModelChoiceField(
        queryset=Property.objects.all(),
        widget=forms.Select(attrs={'class':'form-control','id':'property-select'}),
        help_text="Choose a property to load its vacant units"
    )
    unit = forms.ModelChoiceField(
        queryset=Unit.objects.none(),
        widget=forms.Select(attrs={'class':'form-control','id':'unit-select'})
    )
    start_date = forms.DateField(widget=forms.DateInput(attrs={'class':'form-control','type':'date','min':timezone.now().date().isoformat()}))
    deposit_amount = forms.DecimalField(max_digits=10, decimal_places=2, widget=forms.NumberInput(attrs={'class':'form-control','step':'0.01'}))

    def clean_phone_number(self):
        """Normalize and prevent duplicates."""
        phone = self.cleaned_data.get('phone_number')
        if phone:
            phone = phone.replace(' ', '').replace('-', '')
            if phone.startswith('0'):
                phone = '+254' + phone[1:]
            if Tenant.objects.filter(phone_number=phone).exists():
                raise ValidationError("Tenant with this phone already exists.")
        return phone

    def clean_national_id(self):
        """Strip non-digits and prevent duplicates."""
        nid = self.cleaned_data.get('national_id')
        if nid:
            nid = ''.join(filter(str.isdigit, nid))
            if Tenant.objects.filter(national_id=nid).exists():
                raise ValidationError("Tenant with this national ID already exists.")
        return nid
    
    def __init__(self, *args, **kwargs):
        # Pop initial data first
        initial = kwargs.get('initial', {})
        super().__init__(*args, **kwargs)

        # 1) If we have a property in initial, show all its vacant units
        prop_id = initial.get('property')
        if prop_id:
            self.fields['unit'].queryset = Unit.objects.filter(
                property_id=prop_id,
                is_occupied=False
            )
        # 2) Else if we have a specific unit in initial, allow just that unit
        elif 'unit' in initial:
            self.fields['unit'].queryset = Unit.objects.filter(
                pk=initial['unit']
            )
        # 3) Otherwise keep it empty
        else:
            self.fields['unit'].queryset = Unit.objects.none()
