from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.paginator import Paginator
from django.db import transaction
import json
import pandas as pd
import numpy as np
from decimal import Decimal
import os

from .models import (
    SubdivisionProject, CoordinateData, SubdivisionForm, 
    Parcel, ParcelCoordinate, SubdivisionReport, ProjectActivity
)
from .forms import (
    ProjectUploadForm, CoordinateMappingForm, SubdivisionConfigForm,
    ParcelEditForm, ReportGenerationForm
)


@login_required
def project_list(request):
    """List all subdivision projects"""
    projects = SubdivisionProject.objects.filter(created_by=request.user)
    
    # Pagination
    paginator = Paginator(projects, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    return render(request, 'subdivision/project_list.html', {
        'page_obj': page_obj,
        'projects': page_obj.object_list
    })


@login_required
def project_detail(request, project_id):
    """View project details and status"""
    project = get_object_or_404(SubdivisionProject, project_id=project_id, created_by=request.user)
    
    context = {
        'project': project,
        'coordinates': project.coordinates.all()[:100],  # Limit for performance
        'parcels': project.parcels.all(),
        'activities': project.activities.all()[:20],
        'reports': project.reports.all(),
    }
    
    return render(request, 'subdivision/project_detail.html', context)


@login_required
def step1_upload(request):
    """Step 1: File Upload"""
    if request.method == 'POST':
        form = ProjectUploadForm(request.POST, request.FILES)
        if form.is_valid():
            project = form.save(commit=False)
            project.created_by = request.user
            project.save()
            
            # Log activity
            ProjectActivity.objects.create(
                project=project,
                user=request.user,
                activity_type='created',
                description=f'Project created and file uploaded: {project.coordinate_file.name}'
            )
            
            return redirect('subdivision:step2_mapping', project_id=project.project_id)
    else:
        form = ProjectUploadForm()
    
    return render(request, 'subdivision/step1_upload.html', {'form': form})


def process_coordinate_file(file_path):
    """Simple coordinate file processor"""
    try:
        # Try to read as CSV first
        if file_path.endswith('.csv'):
            df = pd.read_csv(file_path)
        elif file_path.endswith('.xlsx') or file_path.endswith('.xls'):
            df = pd.read_excel(file_path)
        else:
            # Try as text file
            with open(file_path, 'r') as f:
                lines = f.readlines()
            # Simple parsing - assume space or comma separated
            data = []
            for line in lines:
                if line.strip():
                    parts = line.strip().replace(',', ' ').split()
                    if len(parts) >= 3:  # At least point_id, x, y
                        data.append(parts[:4])  # Take first 4 columns max
            df = pd.DataFrame(data)
        
        return df.columns.tolist(), df.head(10).values.tolist()
    except Exception as e:
        return [], []


@login_required
def step2_mapping(request, project_id):
    """Step 2: Coordinate Mapping"""
    project = get_object_or_404(SubdivisionProject, project_id=project_id, created_by=request.user)
    
    # Read file and get columns
    columns, preview_data = process_coordinate_file(project.coordinate_file.path)
    
    if request.method == 'POST':
        form = CoordinateMappingForm(columns, request.POST)
        if form.is_valid():
            # Process coordinates
            try:
                coordinate_count = process_coordinates(project, form.cleaned_data)
                
                project.status = 'processing'
                project.coordinate_system = form.cleaned_data['coordinate_system']
                project.save()
                
                # Log activity
                ProjectActivity.objects.create(
                    project=project,
                    user=request.user,
                    activity_type='processed',
                    description=f'Processed {coordinate_count} coordinate points'
                )
                
                messages.success(request, f'Successfully processed {coordinate_count} coordinates.')
                return redirect('subdivision:step3_visualization', project_id=project.project_id)
                
            except Exception as e:
                messages.error(request, f'Error processing coordinates: {str(e)}')
    else:
        form = CoordinateMappingForm(columns)
    
    context = {
        'project': project,
        'form': form,
        'columns': columns,
        'preview_data': preview_data,
    }
    
    return render(request, 'subdivision/step2_mapping.html', context)


def process_coordinates(project, form_data):
    """Process coordinate data from file"""
    try:
        # Read the file based on extension
        file_path = project.coordinate_file.path
        if file_path.endswith('.csv'):
            df = pd.read_csv(file_path, header=0 if form_data['file_has_headers'] else None)
        elif file_path.endswith('.xlsx') or file_path.endswith('.xls'):
            df = pd.read_excel(file_path, header=0 if form_data['file_has_headers'] else None)
        else:
            with open(file_path, 'r') as f:
                lines = f.readlines()
            data = []
            for line in lines:
                if line.strip():
                    parts = line.strip().replace(',', ' ').split()
                    if len(parts) >= 3:
                        data.append(parts)
            df = pd.DataFrame(data)
        
        # Extract coordinates based on mapping
        point_col = form_data['point_id_column']
        x_col = form_data['x_coordinate_column']
        y_col = form_data['y_coordinate_column']
        z_col = form_data.get('z_coordinate_column')
        
        coordinate_objects = []
        for index, row in df.iterrows():
            try:
                coord = CoordinateData(
                    project=project,
                    point_id=str(row.iloc[point_col]),
                    x_coordinate=Decimal(str(row.iloc[x_col])),
                    y_coordinate=Decimal(str(row.iloc[y_col])),
                    order_index=index
                )
                if z_col is not None and len(row) > z_col:
                    coord.z_coordinate = Decimal(str(row.iloc[z_col]))
                coordinate_objects.append(coord)
            except (ValueError, IndexError):
                continue
        
        # Bulk create coordinates
        CoordinateData.objects.bulk_create(coordinate_objects)
        return len(coordinate_objects)
        
    except Exception as e:
        raise Exception(f"Error processing coordinate file: {str(e)}")


@login_required
def step3_visualization(request, project_id):
    """Step 3: Coordinate Visualization"""
    project = get_object_or_404(SubdivisionProject, project_id=project_id, created_by=request.user)
    
    coordinates = project.coordinates.all().order_by('order_index')
    
    # Prepare coordinate data for visualization
    coord_data = []
    for coord in coordinates:
        coord_data.append({
            'id': coord.point_id,
            'x': float(coord.x_coordinate),
            'y': float(coord.y_coordinate),
            'type': coord.point_type
        })
    
    context = {
        'project': project,
        'coordinate_data': json.dumps(coord_data),
        'coordinate_count': coordinates.count(),
    }
    
    return render(request, 'subdivision/step3_visualization.html', context)


@login_required
def step4_configuration(request, project_id):
    """Step 4: Subdivision Form Configuration"""
    project = get_object_or_404(SubdivisionProject, project_id=project_id, created_by=request.user)
    
    # Calculate land area if not set
    if not project.land_area and project.coordinates.exists():
        project.land_area = calculate_polygon_area(project.coordinates.all())
        project.save()
    
    if request.method == 'POST':
        form = SubdivisionConfigForm(request.POST)
        if form.is_valid():
            config = form.save(commit=False)
            config.project = project
            config.save()
            
            project.status = 'configured'
            project.save()
            
            # Log activity
            ProjectActivity.objects.create(
                project=project,
                user=request.user,
                activity_type='configured',
                description=f'Subdivision configured for {config.num_parcels} parcels'
            )
            
            messages.success(request, 'Subdivision configuration saved successfully.')
            return redirect('subdivision:step5_auto_subdivide', project_id=project.project_id)
    else:
        # Pre-populate with existing config if available
        if hasattr(project, 'form_config'):
            form = SubdivisionConfigForm(instance=project.form_config)
        else:
            form = SubdivisionConfigForm()
    
    context = {
        'project': project,
        'form': form,
    }
    
    return render(request, 'subdivision/step4_configuration.html', context)


def calculate_polygon_area(coordinates):
    """Calculate area using shoelace formula"""
    try:
        coords = [(float(c.x_coordinate), float(c.y_coordinate)) for c in coordinates]
        if len(coords) < 3:
            return Decimal('0')
        
        area = 0
        n = len(coords)
        for i in range(n):
            j = (i + 1) % n
            area += coords[i][0] * coords[j][1]
            area -= coords[j][0] * coords[i][1]
        
        return abs(Decimal(str(area / 2.0)))
    except:
        return Decimal('0')


@login_required
def step5_auto_subdivide(request, project_id):
    """Step 5: Auto-Subdivision"""
    project = get_object_or_404(SubdivisionProject, project_id=project_id, created_by=request.user)
    
    if request.method == 'POST':
        try:
            parcels_created = auto_subdivide_land(project)
            
            project.status = 'subdivided'
            project.save()
            
            # Log activity
            ProjectActivity.objects.create(
                project=project,
                user=request.user,
                activity_type='subdivided',
                description=f'Auto-subdivision complete: {parcels_created} parcels created'
            )
            
            messages.success(request, f'Auto-subdivision complete! Created {parcels_created} parcels.')
            return redirect('subdivision:step6_adjustment', project_id=project.project_id)
            
        except Exception as e:
            messages.error(request, f'Error during auto-subdivision: {str(e)}')
    
    context = {
        'project': project,
        'config': getattr(project, 'form_config', None),
    }
    
    return render(request, 'subdivision/step5_auto_subdivide.html', context)


def auto_subdivide_land(project):
    """Simple auto-subdivision algorithm"""
    config = project.form_config
    coordinates = list(project.coordinates.all().order_by('order_index'))
    
    if len(coordinates) < 3:
        raise Exception("Need at least 3 coordinates to define a boundary")
    
    # Simple grid-based subdivision
    # Get bounding box
    x_coords = [float(c.x_coordinate) for c in coordinates]
    y_coords = [float(c.y_coordinate) for c in coordinates]
    
    min_x, max_x = min(x_coords), max(x_coords)
    min_y, max_y = min(y_coords), max(y_coords)
    
    width = max_x - min_x
    height = max_y - min_y
    
    # Calculate grid dimensions
    if config.layout_pattern == 'grid':
        num_cols = int(np.ceil(np.sqrt(config.num_parcels * width / height)))
        num_rows = int(np.ceil(config.num_parcels / num_cols))
    else:
        num_cols = num_rows = int(np.ceil(np.sqrt(config.num_parcels)))
    
    parcel_width = width / num_cols
    parcel_height = height / num_rows
    
    parcels_created = 0
    parcel_num = 1
    
    for row in range(num_rows):
        for col in range(num_cols):
            if parcels_created >= config.num_parcels:
                break
                
            # Calculate parcel boundaries
            x1 = min_x + col * parcel_width
            y1 = min_y + row * parcel_height
            x2 = x1 + parcel_width
            y2 = y1 + parcel_height
            
            # Create parcel
            parcel = Parcel.objects.create(
                project=project,
                parcel_number=f"LOT-{parcel_num:03d}",
                area=Decimal(str(parcel_width * parcel_height)),
                perimeter=Decimal(str(2 * (parcel_width + parcel_height))),
                parcel_type='lot'
            )
            
            # Create parcel coordinates (rectangle)
            coords = [
                (x1, y1), (x2, y1), (x2, y2), (x1, y2)
            ]
            
            for i, (x, y) in enumerate(coords):
                ParcelCoordinate.objects.create(
                    parcel=parcel,
                    x_coordinate=Decimal(str(x)),
                    y_coordinate=Decimal(str(y)),
                    order_index=i
                )
            
            parcels_created += 1
            parcel_num += 1
            
        if parcels_created >= config.num_parcels:
            break
    
    return parcels_created


@login_required
def step6_adjustment(request, project_id):
    """Step 6: Manual Adjustment Interface"""
    project = get_object_or_404(SubdivisionProject, project_id=project_id, created_by=request.user)
    
    parcels = project.parcels.all()
    
    # Prepare parcel data for visualization
    parcel_data = []
    for parcel in parcels:
        coords = []
        for coord in parcel.coordinates.all():
            coords.append([float(coord.x_coordinate), float(coord.y_coordinate)])
        
        parcel_data.append({
            'id': str(parcel.id),
            'number': parcel.parcel_number,
            'area': float(parcel.area),
            'type': parcel.parcel_type,
            'approved': parcel.is_approved,
            'coordinates': coords
        })
    
    context = {
        'project': project,
        'parcels': parcels,
        'parcel_data': json.dumps(parcel_data),
    }
    
    return render(request, 'subdivision/step6_adjustment.html', context)


@login_required
def step7_deliverables(request, project_id):
    """Step 7: Generate Deliverables"""
    project = get_object_or_404(SubdivisionProject, project_id=project_id, created_by=request.user)
    
    if request.method == 'POST':
        form = ReportGenerationForm(request.POST)
        if form.is_valid():
            try:
                reports = generate_reports(
                    project,
                    form.cleaned_data['report_types'],
                    {
                        'include_coordinates': form.cleaned_data['include_coordinates'],
                        'include_areas': form.cleaned_data['include_areas'],
                        'scale': form.cleaned_data['scale'],
                        'generated_by': request.user
                    }
                )
                
                project.status = 'completed'
                project.save()
                
                # Log activity
                ProjectActivity.objects.create(
                    project=project,
                    user=request.user,
                    activity_type='completed',
                    description=f'Project completed. Generated {len(reports)} deliverable(s)'
                )
                
                messages.success(request, f'Successfully generated {len(reports)} deliverable(s).')
                return redirect('subdivision:project_detail', project_id=project.project_id)
                
            except Exception as e:
                messages.error(request, f'Error generating reports: {str(e)}')
    else:
        form = ReportGenerationForm()
    
    context = {
        'project': project,
        'form': form,
        'existing_reports': project.reports.all(),
    }
    
    return render(request, 'subdivision/step7_deliverables.html', context)


def generate_reports(project, report_types, options):
    """Generate project reports"""
    reports = []
    
    for report_type in report_types:
        try:
            if report_type == 'parcel_list':
                # Generate CSV parcel list
                import csv
                import tempfile
                
                temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
                writer = csv.writer(temp_file)
                
                # Write header
                writer.writerow(['Parcel Number', 'Area (sq m)', 'Perimeter (m)', 'Type', 'Status'])
                
                # Write parcel data
                for parcel in project.parcels.all():
                    writer.writerow([
                        parcel.parcel_number,
                        str(parcel.area),
                        str(parcel.perimeter),
                        parcel.get_parcel_type_display(),
                        'Approved' if parcel.is_approved else 'Pending'
                    ])
                
                temp_file.close()
                
                # Create report record
                with open(temp_file.name, 'rb') as f:
                    report = SubdivisionReport.objects.create(
                        project=project,
                        report_type=report_type,
                        file_format='CSV',
                        generated_by=options['generated_by']
                    )
                    report.file_path.save(
                        f'{project.name}_parcel_list.csv',
                        f,
                        save=True
                    )
                    reports.append(report)
                
                os.unlink(temp_file.name)
                
            elif report_type == 'coordinate_list':
                # Generate CSV coordinate list
                import csv
                import tempfile
                
                temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
                writer = csv.writer(temp_file)
                
                # Write header
                writer.writerow(['Point ID', 'X Coordinate', 'Y Coordinate', 'Z Coordinate', 'Type'])
                
                # Write coordinate data
                for coord in project.coordinates.all():
                    writer.writerow([
                        coord.point_id,
                        str(coord.x_coordinate),
                        str(coord.y_coordinate),
                        str(coord.z_coordinate or ''),
                        coord.point_type
                    ])
                
                temp_file.close()
                
                # Create report record
                with open(temp_file.name, 'rb') as f:
                    report = SubdivisionReport.objects.create(
                        project=project,
                        report_type=report_type,
                        file_format='CSV',
                        generated_by=options['generated_by']
                    )
                    report.file_path.save(
                        f'{project.name}_coordinates.csv',
                        f,
                        save=True
                    )
                    reports.append(report)
                
                os.unlink(temp_file.name)
        
        except Exception as e:
            print(f"Error generating {report_type}: {e}")
            continue
    
    return reports


# AJAX Views
@csrf_exempt
@login_required
def update_parcel_ajax(request, project_id, parcel_id):
    """AJAX view to update parcel properties"""
    if request.method == 'POST':
        try:
            project = get_object_or_404(SubdivisionProject, project_id=project_id, created_by=request.user)
            parcel = get_object_or_404(Parcel, id=parcel_id, project=project)
            
            data = json.loads(request.body)
            
            # Update parcel properties
            if 'parcel_number' in data:
                parcel.parcel_number = data['parcel_number']
            if 'parcel_type' in data:
                parcel.parcel_type = data['parcel_type']
            if 'is_approved' in data:
                parcel.is_approved = data['is_approved']
            
            parcel.is_modified = True
            parcel.save()
            
            return JsonResponse({'success': True})
            
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})


@login_required
def download_report(request, project_id, report_id):
    """Download a generated report"""
    project = get_object_or_404(SubdivisionProject, project_id=project_id, created_by=request.user)
    report = get_object_or_404(SubdivisionReport, id=report_id, project=project)
    
    if os.path.exists(report.file_path.path):
        with open(report.file_path.path, 'rb') as f:
            response = HttpResponse(f.read(), content_type='application/octet-stream')
            response['Content-Disposition'] = f'attachment; filename="{os.path.basename(report.file_path.path)}"'
            return response
    else:
        messages.error(request, 'Report file not found.')
        return redirect('subdivision:project_detail', project_id=project.project_id)
