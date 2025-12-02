from django import forms
from django.core.validators import FileExtensionValidator
from .models import SubdivisionProject, SubdivisionForm, Parcel


class ProjectUploadForm(forms.ModelForm):
    """Form for uploading coordinate files"""
    
    class Meta:
        model = SubdivisionProject
        fields = ['name', 'description', 'coordinate_file']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter project name',
                'required': True
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Enter project description (optional)'
            }),
            'coordinate_file': forms.FileInput(attrs={
                'class': 'form-control',
                'accept': '.csv,.txt,.xlsx,.xls',
                'required': True
            })
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['coordinate_file'].validators = [
            FileExtensionValidator(allowed_extensions=['csv', 'txt', 'xlsx', 'xls'])
        ]
        self.fields['coordinate_file'].help_text = "Supported formats: CSV, TXT, Excel (.xlsx, .xls)"


class CoordinateMappingForm(forms.Form):
    """Form for mapping coordinate columns"""
    
    file_has_headers = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text="Check if the first row contains column headers"
    )
    
    point_id_column = forms.IntegerField(
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text="Column containing point IDs"
    )
    
    x_coordinate_column = forms.IntegerField(
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text="Column containing X coordinates (Easting)"
    )
    
    y_coordinate_column = forms.IntegerField(
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text="Column containing Y coordinates (Northing)"
    )
    
    z_coordinate_column = forms.IntegerField(
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text="Column containing Z coordinates (Elevation) - Optional"
    )
    
    coordinate_system = forms.ChoiceField(
        choices=[
            ('', 'Select Coordinate System'),
            ('UTM_WGS84', 'UTM WGS84'),
            ('State_Plane', 'State Plane'),
            ('Local_Grid', 'Local Grid System'),
            ('Geographic', 'Geographic (Lat/Long)'),
        ],
        widget=forms.Select(attrs={'class': 'form-select'}),
        required=True
    )
    
    def __init__(self, columns=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        if columns:
            choices = [(i, f"Column {i+1}: {col}") for i, col in enumerate(columns)]
            choices.insert(0, ('', 'Select Column'))
            
            self.fields['point_id_column'].widget.choices = choices
            self.fields['x_coordinate_column'].widget.choices = choices
            self.fields['y_coordinate_column'].widget.choices = choices
            self.fields['z_coordinate_column'].widget.choices = choices


class SubdivisionConfigForm(forms.ModelForm):
    """Form for configuring AI-powered subdivision parameters"""
    
    class Meta:
        model = SubdivisionForm
        fields = [
            'plot_width', 'plot_length', 'plot_count_mode', 'specified_plot_count',
            'orientation', 'custom_angle', 'road_setback_front', 'road_setback_side', 
            'road_setback_rear', 'internal_road_width', 'use_satellite_imagery',
            'terrain_awareness', 'road_detection', 'preserve_features', 'fitting_strategy',
            'preserve_trees', 'water_access', 'slope_constraints'
        ]
        widgets = {
            'plot_width': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': 0.01,
                'placeholder': 'Plot width (meters)'
            }),
            'plot_length': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': 0.01,
                'placeholder': 'Plot length (meters)'
            }),
            'plot_count_mode': forms.Select(attrs={'class': 'form-select'}),
            'specified_plot_count': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': 1,
                'max': 1000,
                'placeholder': 'Number of plots'
            }),
            'orientation': forms.Select(attrs={'class': 'form-select'}),
            'custom_angle': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': 0.1,
                'placeholder': 'Custom angle in degrees'
            }),
            'road_setback_front': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': 0.1,
                'placeholder': 'Front setback (meters)'
            }),
            'road_setback_side': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': 0.1,
                'placeholder': 'Side setback (meters)'
            }),
            'road_setback_rear': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': 0.1,
                'placeholder': 'Rear setback (meters)'
            }),
            'internal_road_width': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': 0.1,
                'placeholder': 'Internal road width (meters)'
            }),
            'fitting_strategy': forms.Select(attrs={'class': 'form-select'}),
            'use_satellite_imagery': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'terrain_awareness': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'road_detection': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'preserve_features': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'preserve_trees': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'water_access': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'slope_constraints': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class ParcelEditForm(forms.ModelForm):
    """Form for editing individual parcels"""
    
    class Meta:
        model = Parcel
        fields = ['parcel_number', 'parcel_type', 'is_approved']
        widgets = {
            'parcel_number': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Parcel number'
            }),
            'parcel_type': forms.Select(attrs={'class': 'form-select'}),
            'is_approved': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class ReportGenerationForm(forms.Form):
    """Form for selecting reports to generate"""
    
    REPORT_CHOICES = [
        ('survey_plan', 'Survey Plan (PDF)'),
        ('parcel_list', 'Parcel List (CSV)'),
        ('area_schedule', 'Area Schedule (PDF)'),
        ('coordinate_list', 'Coordinate List (CSV)'),
        ('technical_report', 'Technical Report (PDF)'),
        ('cad_drawing', 'CAD Drawing (DWG)'),
    ]
    
    report_types = forms.MultipleChoiceField(
        choices=REPORT_CHOICES,
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-check-input'}),
        required=True,
        help_text="Select the reports you want to generate"
    )
    
    include_coordinates = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text="Include detailed coordinate information"
    )
    
    include_areas = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text="Include area calculations"
    )
    
    scale = forms.ChoiceField(
        choices=[
            ('1:500', '1:500'),
            ('1:1000', '1:1000'),
            ('1:2000', '1:2000'),
            ('1:5000', '1:5000'),
            ('auto', 'Auto Scale'),
        ],
        initial='auto',
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text="Map scale for PDF reports"
    )