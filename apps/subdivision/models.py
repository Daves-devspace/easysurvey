from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator, MaxValueValidator
import uuid
import os


def subdivision_upload_path(instance, filename):
    """Generate upload path for subdivision files"""
    return f'subdivisions/{instance.project_id}/{filename}'


class SubdivisionProject(models.Model):
    """Main subdivision project model"""
    project_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # File uploads
    coordinate_file = models.FileField(
        upload_to=subdivision_upload_path,
        help_text="Upload coordinate file (CSV, TXT, or Excel)"
    )
    
    # Project status - Enhanced workflow
    STATUS_CHOICES = [
        ('uploaded', 'Parent Plot Uploaded'),
        ('mapped', 'Parcel Mapped & Analyzed'),
        ('inspected', 'Boundaries Inspected'),
        ('configured', 'Subdivision Parameters Set'),
        ('plotted', 'AI Auto-Subdivision Complete'),
        ('adjusted', 'Interactive Adjustments Made'),
        ('approved', 'Layout Approved'),
        ('completed', 'Final Outputs Generated'),
        ('archived', 'Project Archived'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='uploaded')
    
    # Enhanced Configuration
    coordinate_system = models.CharField(max_length=50, blank=True)
    detected_crs = models.CharField(max_length=100, blank=True)  # Auto-detected CRS
    land_area = models.DecimalField(max_digits=15, decimal_places=6, null=True, blank=True)
    perimeter = models.DecimalField(max_digits=15, decimal_places=6, null=True, blank=True)
    
    # Satellite/Map settings
    center_latitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    center_longitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    map_zoom_level = models.IntegerField(default=18)
    
    # AI Processing flags
    ai_processing_complete = models.BooleanField(default=False)
    terrain_analysis_complete = models.BooleanField(default=False)
    road_detection_complete = models.BooleanField(default=False)
    
    class Meta:
        ordering = ['-created_at']
        
    def __str__(self):
        return f"{self.name} - {self.get_status_display()}"


class CoordinateData(models.Model):
    """Store processed coordinate data"""
    project = models.ForeignKey(SubdivisionProject, on_delete=models.CASCADE, related_name='coordinates')
    point_id = models.CharField(max_length=50)
    x_coordinate = models.DecimalField(max_digits=15, decimal_places=6)
    y_coordinate = models.DecimalField(max_digits=15, decimal_places=6)
    z_coordinate = models.DecimalField(max_digits=15, decimal_places=6, null=True, blank=True)
    point_type = models.CharField(max_length=20, default='boundary')  # boundary, interior, control
    order_index = models.IntegerField(default=0)
    
    class Meta:
        ordering = ['order_index', 'point_id']
        unique_together = ['project', 'point_id']
        
    def __str__(self):
        return f"{self.project.name} - {self.point_id}"


class SubdivisionForm(models.Model):
    """Enhanced subdivision parameters following AI-powered workflow"""
    project = models.OneToOneField(SubdivisionProject, on_delete=models.CASCADE, related_name='form_config')
    
    # Plot Specifications
    plot_width = models.DecimalField(max_digits=8, decimal_places=2, default=50.0, help_text="Plot width in meters (e.g., 50)")
    plot_length = models.DecimalField(max_digits=8, decimal_places=2, default=100.0, help_text="Plot length in meters (e.g., 100)")
    
    # Number Configuration
    PLOT_COUNT_CHOICES = [
        ('specify_count', 'Specify Number of Plots'),
        ('auto_fit', 'Auto-fit Maximum Plots'),
    ]
    plot_count_mode = models.CharField(max_length=20, choices=PLOT_COUNT_CHOICES, default='auto_fit')
    specified_plot_count = models.IntegerField(null=True, blank=True, validators=[MinValueValidator(1)])
    
    # Orientation & Layout
    ORIENTATION_CHOICES = [
        ('north', 'North-South Orientation'),
        ('east', 'East-West Orientation'),
        ('optimal', 'AI-Optimal Orientation'),
        ('custom', 'Custom Angle'),
    ]
    orientation = models.CharField(max_length=20, choices=ORIENTATION_CHOICES, default='optimal')
    custom_angle = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, help_text="Custom angle in degrees")
    
    # Road & Access Requirements
    road_setback_front = models.DecimalField(max_digits=5, decimal_places=2, default=5.0, help_text="Front setback from road (m)")
    road_setback_side = models.DecimalField(max_digits=5, decimal_places=2, default=2.0, help_text="Side setback (m)")
    road_setback_rear = models.DecimalField(max_digits=5, decimal_places=2, default=3.0, help_text="Rear setback (m)")
    internal_road_width = models.DecimalField(max_digits=5, decimal_places=2, default=8.0, help_text="Internal access road width")
    
    # AI Processing Preferences
    use_satellite_imagery = models.BooleanField(default=True, help_text="Use satellite data for terrain analysis")
    terrain_awareness = models.BooleanField(default=True, help_text="Consider terrain in subdivision")
    road_detection = models.BooleanField(default=True, help_text="Auto-detect existing roads")
    preserve_features = models.BooleanField(default=True, help_text="Preserve natural features when possible")
    
    # Smart Fitting Options
    FITTING_STRATEGY_CHOICES = [
        ('full_plots_only', 'Take Only Full Plots That Fit'),
        ('resize_equally', 'Resize All Plots Equally'),
        ('reduce_plot_size', 'Reduce Individual Plot Size'),
        ('mixed_approach', 'AI-Mixed Approach'),
    ]
    fitting_strategy = models.CharField(max_length=20, choices=FITTING_STRATEGY_CHOICES, default='mixed_approach')
    
    # Additional Configuration
    preserve_trees = models.BooleanField(default=False)
    water_access = models.BooleanField(default=False)
    slope_constraints = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"Form Config - {self.project.name}"


class Parcel(models.Model):
    """Individual parcel within a subdivision"""
    project = models.ForeignKey(SubdivisionProject, on_delete=models.CASCADE, related_name='parcels')
    parcel_number = models.CharField(max_length=20)
    
    # Geometry (stored as coordinate points)
    area = models.DecimalField(max_digits=12, decimal_places=6)
    perimeter = models.DecimalField(max_digits=12, decimal_places=6)
    
    # Status
    is_approved = models.BooleanField(default=True)
    is_modified = models.BooleanField(default=False)
    
    # Classification
    parcel_type = models.CharField(
        max_length=20,
        choices=[
            ('lot', 'Building Lot'),
            ('road', 'Road/Access'),
            ('utility', 'Utility Easement'),
            ('green', 'Green Space'),
            ('common', 'Common Area'),
        ],
        default='lot'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['parcel_number']
        unique_together = ['project', 'parcel_number']
        
    def __str__(self):
        return f"Parcel {self.parcel_number} - {self.project.name}"


class ParcelCoordinate(models.Model):
    """Coordinates defining parcel boundaries"""
    parcel = models.ForeignKey(Parcel, on_delete=models.CASCADE, related_name='coordinates')
    x_coordinate = models.DecimalField(max_digits=15, decimal_places=6)
    y_coordinate = models.DecimalField(max_digits=15, decimal_places=6)
    order_index = models.IntegerField()
    
    class Meta:
        ordering = ['order_index']
        
    def __str__(self):
        return f"{self.parcel.parcel_number} - Point {self.order_index}"


class SubdivisionReport(models.Model):
    """Generated reports and deliverables"""
    project = models.ForeignKey(SubdivisionProject, on_delete=models.CASCADE, related_name='reports')
    
    report_type = models.CharField(
        max_length=30,
        choices=[
            ('survey_plan', 'Survey Plan'),
            ('parcel_list', 'Parcel List'),
            ('area_schedule', 'Area Schedule'),
            ('coordinate_list', 'Coordinate List'),
            ('technical_report', 'Technical Report'),
            ('cad_drawing', 'CAD Drawing'),
        ]
    )
    
    file_path = models.FileField(upload_to=subdivision_upload_path)
    file_format = models.CharField(max_length=10)  # PDF, DWG, CSV, etc.
    
    generated_at = models.DateTimeField(auto_now_add=True)
    generated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    
    class Meta:
        ordering = ['-generated_at']
        
    def __str__(self):
        return f"{self.get_report_type_display()} - {self.project.name}"


class ProjectActivity(models.Model):
    """Activity log for subdivision projects"""
    project = models.ForeignKey(SubdivisionProject, on_delete=models.CASCADE, related_name='activities')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    
    activity_type = models.CharField(
        max_length=30,
        choices=[
            ('created', 'Project Created'),
            ('uploaded', 'File Uploaded'),
            ('processed', 'Coordinates Processed'),
            ('configured', 'Form Configured'),
            ('subdivided', 'Auto-Subdivision Complete'),
            ('modified', 'Manual Modifications'),
            ('report_generated', 'Report Generated'),
            ('completed', 'Project Completed'),
        ]
    )
    
    description = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-timestamp']
        
    def __str__(self):
        return f"{self.project.name} - {self.get_activity_type_display()}"


class BeaconPoint(models.Model):
    """Individual beacon points with enhanced tracking"""
    project = models.ForeignKey(SubdivisionProject, on_delete=models.CASCADE, related_name='beacons')
    beacon_id = models.CharField(max_length=20)  # e.g., BP001, BP002
    
    # Coordinates
    x_coordinate = models.DecimalField(max_digits=15, decimal_places=6)
    y_coordinate = models.DecimalField(max_digits=15, decimal_places=6)
    z_coordinate = models.DecimalField(max_digits=15, decimal_places=6, null=True, blank=True)
    
    # Beacon Classification
    BEACON_TYPES = [
        ('boundary', 'Boundary Beacon'),
        ('plot_corner', 'Plot Corner'),
        ('road_marker', 'Road Marker'),
        ('utility_point', 'Utility Point'),
        ('control_point', 'Control Point'),
    ]
    beacon_type = models.CharField(max_length=20, choices=BEACON_TYPES, default='boundary')
    
    # Status tracking
    is_existing = models.BooleanField(default=False)  # Existing vs new beacon
    requires_setting = models.BooleanField(default=True)  # Physical setting required
    is_verified = models.BooleanField(default=False)  # Field verification
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ['project', 'beacon_id']
        ordering = ['beacon_id']
    
    def __str__(self):
        return f"{self.beacon_id} - {self.project.name}"


class TerrainAnalysis(models.Model):
    """AI terrain analysis results"""
    project = models.OneToOneField(SubdivisionProject, on_delete=models.CASCADE, related_name='terrain_analysis')
    
    # Elevation data
    min_elevation = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    max_elevation = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    avg_slope = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    max_slope = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    
    # Drainage and water features
    has_water_features = models.BooleanField(default=False)
    drainage_direction = models.CharField(max_length=20, blank=True)  # N, S, E, W, NE, etc.
    flood_risk_level = models.CharField(max_length=10, choices=[
        ('low', 'Low Risk'),
        ('medium', 'Medium Risk'),
        ('high', 'High Risk'),
    ], default='low')
    
    # Vegetation and features
    vegetation_coverage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)  # Percentage
    has_mature_trees = models.BooleanField(default=False)
    has_buildings = models.BooleanField(default=False)
    
    # Analysis metadata
    analysis_date = models.DateTimeField(auto_now_add=True)
    data_source = models.CharField(max_length=100, blank=True)  # Satellite provider, etc.
    confidence_score = models.DecimalField(max_digits=5, decimal_places=2, default=0.0)
    
    def __str__(self):
        return f"Terrain Analysis - {self.project.name}"


class RoadNetwork(models.Model):
    """Detected and planned road network"""
    project = models.ForeignKey(SubdivisionProject, on_delete=models.CASCADE, related_name='roads')
    road_name = models.CharField(max_length=100, blank=True)
    
    # Road classification
    ROAD_TYPES = [
        ('existing_major', 'Existing Major Road'),
        ('existing_minor', 'Existing Minor Road'),
        ('proposed_main', 'Proposed Main Access'),
        ('proposed_internal', 'Proposed Internal Road'),
        ('service_road', 'Service/Utility Road'),
    ]
    road_type = models.CharField(max_length=20, choices=ROAD_TYPES)
    
    # Road specifications
    width = models.DecimalField(max_digits=5, decimal_places=2)
    length = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    surface_type = models.CharField(max_length=20, choices=[
        ('paved', 'Paved'),
        ('gravel', 'Gravel'),
        ('dirt', 'Dirt/Earth'),
        ('proposed', 'Proposed Construction'),
    ], default='proposed')
    
    # Geometry (stored as coordinate arrays)
    start_x = models.DecimalField(max_digits=15, decimal_places=6)
    start_y = models.DecimalField(max_digits=15, decimal_places=6)
    end_x = models.DecimalField(max_digits=15, decimal_places=6)
    end_y = models.DecimalField(max_digits=15, decimal_places=6)
    
    # Planning
    is_public_access = models.BooleanField(default=True)
    requires_easement = models.BooleanField(default=False)
    
    def __str__(self):
        return f"{self.road_name or 'Road'} - {self.get_road_type_display()}"


class SubdivisionIssue(models.Model):
    """Smart issue tracking and resolution suggestions"""
    project = models.ForeignKey(SubdivisionProject, on_delete=models.CASCADE, related_name='issues')
    
    # Issue classification
    ISSUE_TYPES = [
        ('size_mismatch', 'Plot Size Mismatch'),
        ('count_mismatch', 'Plot Count Mismatch'),
        ('access_problem', 'Access/Road Issue'),
        ('terrain_constraint', 'Terrain Constraint'),
        ('setback_violation', 'Setback Violation'),
        ('geometry_conflict', 'Geometry Conflict'),
        ('regulation_issue', 'Regulation Compliance'),
    ]
    issue_type = models.CharField(max_length=20, choices=ISSUE_TYPES)
    
    # Issue details
    severity = models.CharField(max_length=10, choices=[
        ('low', 'Low - Advisory'),
        ('medium', 'Medium - Attention Required'),
        ('high', 'High - Must Resolve'),
        ('critical', 'Critical - Blocking'),
    ])
    
    description = models.TextField()
    affected_area = models.TextField(blank=True)  # JSON of affected coordinates/plots
    
    # AI Suggestions
    suggested_solutions = models.JSONField(default=list, blank=True)  # Array of solution objects
    auto_fix_available = models.BooleanField(default=False)
    
    # Resolution tracking
    STATUS_CHOICES = [
        ('open', 'Open'),
        ('reviewing', 'Under Review'),
        ('resolved', 'Resolved'),
        ('ignored', 'Ignored'),
        ('deferred', 'Deferred'),
    ]
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='open')
    
    # Metadata
    detected_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    
    class Meta:
        ordering = ['-severity', '-detected_at']
    
    def __str__(self):
        return f"{self.get_issue_type_display()} - {self.severity}"


class ClientDeliverable(models.Model):
    """Client-ready outputs and previews"""
    project = models.ForeignKey(SubdivisionProject, on_delete=models.CASCADE, related_name='deliverables')
    
    DELIVERABLE_TYPES = [
        ('mutation_map', 'Mutation Map (PDF)'),
        ('beacon_list', 'Beacon Coordinate List'),
        ('area_summary', 'Area Summary Report'),
        ('gis_export', 'GIS Data Export'),
        ('client_preview', 'Client Preview Package'),
        ('survey_report', 'Professional Survey Report'),
        ('cad_drawings', 'CAD/DWG Files'),
    ]
    deliverable_type = models.CharField(max_length=20, choices=DELIVERABLE_TYPES)
    
    # File details
    file_path = models.FileField(upload_to=subdivision_upload_path)
    file_format = models.CharField(max_length=10)  # PDF, DWG, CSV, SHP, etc.
    file_size = models.IntegerField(default=0)  # Size in bytes
    
    # Client interaction
    sent_to_client = models.BooleanField(default=False)
    client_approved = models.BooleanField(default=False)
    client_feedback = models.TextField(blank=True)
    
    # Generation metadata
    generated_at = models.DateTimeField(auto_now_add=True)
    generated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    version_number = models.IntegerField(default=1)
    
    # Quality metrics
    accuracy_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    completeness_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    
    class Meta:
        ordering = ['-generated_at']
        unique_together = ['project', 'deliverable_type', 'version_number']
    
    def __str__(self):
        return f"{self.get_deliverable_type_display()} v{self.version_number} - {self.project.name}"


class SessionLog(models.Model):
    """Detailed session logging for audit trail"""
    project = models.ForeignKey(SubdivisionProject, on_delete=models.CASCADE, related_name='session_logs')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    
    # Session details
    session_start = models.DateTimeField(auto_now_add=True)
    session_end = models.DateTimeField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    
    # Actions performed
    actions_performed = models.JSONField(default=list)  # Array of action objects
    changes_made = models.JSONField(default=dict)  # Object of change tracking
    
    # Performance metrics
    processing_time = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)  # Seconds
    api_calls_made = models.IntegerField(default=0)
    
    def __str__(self):
        return f"Session {self.user.username} - {self.session_start.strftime('%Y-%m-%d %H:%M')}"
