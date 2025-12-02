from django.contrib import admin
from .models import (
    SubdivisionProject, CoordinateData, SubdivisionForm,
    Parcel, ParcelCoordinate, SubdivisionReport, ProjectActivity,
    BeaconPoint, TerrainAnalysis, RoadNetwork, SubdivisionIssue,
    ClientDeliverable, SessionLog
)


@admin.register(SubdivisionProject)
class SubdivisionProjectAdmin(admin.ModelAdmin):
    list_display = ['name', 'status', 'created_by', 'created_at', 'coordinate_system']
    list_filter = ['status', 'created_at', 'coordinate_system']
    search_fields = ['name', 'description']
    readonly_fields = ['project_id', 'created_at', 'updated_at']
    
    fieldsets = (
        ('Project Information', {
            'fields': ('project_id', 'name', 'description', 'status')
        }),
        ('File Upload', {
            'fields': ('coordinate_file', 'coordinate_system', 'land_area')
        }),
        ('Metadata', {
            'fields': ('created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(CoordinateData)
class CoordinateDataAdmin(admin.ModelAdmin):
    list_display = ['project', 'point_id', 'x_coordinate', 'y_coordinate', 'point_type']
    list_filter = ['point_type', 'project__name']
    search_fields = ['point_id', 'project__name']
    ordering = ['project', 'order_index']


@admin.register(SubdivisionForm)
class SubdivisionFormAdmin(admin.ModelAdmin):
    list_display = ['project', 'plot_width', 'plot_length', 'plot_count_mode', 'orientation']
    list_filter = ['plot_count_mode', 'orientation', 'fitting_strategy']
    search_fields = ['project__name']
    
    fieldsets = (
        ('Plot Specifications', {
            'fields': ('project', 'plot_width', 'plot_length')
        }),
        ('Count & Layout', {
            'fields': ('plot_count_mode', 'specified_plot_count', 'orientation', 'custom_angle')
        }),
        ('Setbacks & Roads', {
            'fields': ('road_setback_front', 'road_setback_side', 'road_setback_rear', 'internal_road_width')
        }),
        ('AI Processing', {
            'fields': ('use_satellite_imagery', 'terrain_awareness', 'road_detection', 'preserve_features', 'fitting_strategy')
        }),
        ('Additional Options', {
            'fields': ('preserve_trees', 'water_access', 'slope_constraints')
        }),
    )


class ParcelCoordinateInline(admin.TabularInline):
    model = ParcelCoordinate
    extra = 0
    ordering = ['order_index']


@admin.register(Parcel)
class ParcelAdmin(admin.ModelAdmin):
    list_display = ['parcel_number', 'project', 'area', 'parcel_type', 'is_approved']
    list_filter = ['parcel_type', 'is_approved', 'is_modified']
    search_fields = ['parcel_number', 'project__name']
    inlines = [ParcelCoordinateInline]


@admin.register(SubdivisionReport)
class SubdivisionReportAdmin(admin.ModelAdmin):
    list_display = ['project', 'report_type', 'file_format', 'generated_at', 'generated_by']
    list_filter = ['report_type', 'file_format', 'generated_at']
    search_fields = ['project__name']
    readonly_fields = ['generated_at']


@admin.register(ProjectActivity)
class ProjectActivityAdmin(admin.ModelAdmin):
    list_display = ['project', 'activity_type', 'user', 'timestamp']
    list_filter = ['activity_type', 'timestamp']
    search_fields = ['project__name', 'description']
    readonly_fields = ['timestamp']


@admin.register(BeaconPoint)
class BeaconPointAdmin(admin.ModelAdmin):
    list_display = ['beacon_id', 'project', 'beacon_type', 'is_existing', 'requires_setting', 'is_verified']
    list_filter = ['beacon_type', 'is_existing', 'requires_setting', 'is_verified']
    search_fields = ['beacon_id', 'project__name']
    ordering = ['project', 'beacon_id']
    
    fieldsets = (
        ('Beacon Information', {
            'fields': ('project', 'beacon_id', 'beacon_type')
        }),
        ('Coordinates', {
            'fields': ('x_coordinate', 'y_coordinate', 'z_coordinate')
        }),
        ('Status', {
            'fields': ('is_existing', 'requires_setting', 'is_verified')
        }),
    )


@admin.register(TerrainAnalysis)
class TerrainAnalysisAdmin(admin.ModelAdmin):
    list_display = ['project', 'confidence_score', 'has_water_features', 'flood_risk_level', 'analysis_date']
    list_filter = ['flood_risk_level', 'has_water_features', 'has_mature_trees', 'has_buildings']
    search_fields = ['project__name']
    readonly_fields = ['analysis_date']
    
    fieldsets = (
        ('Project', {
            'fields': ('project',)
        }),
        ('Elevation Data', {
            'fields': ('min_elevation', 'max_elevation', 'avg_slope', 'max_slope')
        }),
        ('Water Features', {
            'fields': ('has_water_features', 'drainage_direction', 'flood_risk_level')
        }),
        ('Vegetation & Features', {
            'fields': ('vegetation_coverage', 'has_mature_trees', 'has_buildings')
        }),
        ('Analysis Metadata', {
            'fields': ('analysis_date', 'data_source', 'confidence_score'),
            'classes': ('collapse',)
        }),
    )


@admin.register(RoadNetwork)
class RoadNetworkAdmin(admin.ModelAdmin):
    list_display = ['road_name', 'project', 'road_type', 'width', 'surface_type', 'is_public_access']
    list_filter = ['road_type', 'surface_type', 'is_public_access', 'requires_easement']
    search_fields = ['road_name', 'project__name']
    
    fieldsets = (
        ('Road Information', {
            'fields': ('project', 'road_name', 'road_type')
        }),
        ('Specifications', {
            'fields': ('width', 'length', 'surface_type')
        }),
        ('Coordinates', {
            'fields': ('start_x', 'start_y', 'end_x', 'end_y')
        }),
        ('Access & Easements', {
            'fields': ('is_public_access', 'requires_easement')
        }),
    )


@admin.register(SubdivisionIssue)
class SubdivisionIssueAdmin(admin.ModelAdmin):
    list_display = ['issue_type', 'severity', 'status', 'project', 'auto_fix_available', 'detected_at']
    list_filter = ['issue_type', 'severity', 'status', 'auto_fix_available']
    search_fields = ['project__name', 'description']
    readonly_fields = ['detected_at', 'resolved_at']
    ordering = ['-severity', '-detected_at']
    
    fieldsets = (
        ('Issue Details', {
            'fields': ('project', 'issue_type', 'severity', 'description')
        }),
        ('AI Analysis', {
            'fields': ('affected_area', 'suggested_solutions', 'auto_fix_available')
        }),
        ('Resolution', {
            'fields': ('status', 'resolved_at', 'resolved_by')
        }),
    )


@admin.register(ClientDeliverable)
class ClientDeliverableAdmin(admin.ModelAdmin):
    list_display = ['deliverable_type', 'project', 'version_number', 'file_format', 'sent_to_client', 'client_approved', 'generated_at']
    list_filter = ['deliverable_type', 'file_format', 'sent_to_client', 'client_approved']
    search_fields = ['project__name']
    readonly_fields = ['generated_at', 'file_size']
    ordering = ['-generated_at']
    
    fieldsets = (
        ('Deliverable Information', {
            'fields': ('project', 'deliverable_type', 'version_number')
        }),
        ('File Details', {
            'fields': ('file_path', 'file_format', 'file_size')
        }),
        ('Client Interaction', {
            'fields': ('sent_to_client', 'client_approved', 'client_feedback')
        }),
        ('Quality Metrics', {
            'fields': ('accuracy_score', 'completeness_score'),
            'classes': ('collapse',)
        }),
        ('Generation Info', {
            'fields': ('generated_at', 'generated_by'),
            'classes': ('collapse',)
        }),
    )


@admin.register(SessionLog)
class SessionLogAdmin(admin.ModelAdmin):
    list_display = ['user', 'project', 'session_start', 'session_end', 'processing_time']
    list_filter = ['session_start', 'user']
    search_fields = ['user__username', 'project__name', 'ip_address']
    readonly_fields = ['session_start', 'session_end']
    ordering = ['-session_start']
    
    def has_add_permission(self, request):
        return False  # Session logs should be system-generated only
