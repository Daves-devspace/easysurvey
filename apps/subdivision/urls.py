from django.urls import path
from . import views

app_name = 'subdivision'

urlpatterns = [
    # Project management
    path('', views.project_list, name='project_list'),
    path('project/<uuid:pk>/', views.project_detail, name='project_detail'),
    
    # Subdivision workflow steps  
    path('step1/upload/', views.step1_upload, name='step1_upload'),
    path('step2/mapping/<uuid:pk>/', views.step2_mapping, name='step2_mapping'), 
    path('step3/visualization/<uuid:pk>/', views.step3_visualization, name='step3_visualization'),
    path('step4/configuration/<uuid:pk>/', views.step4_configuration, name='step4_configuration'),
    path('step5/auto-subdivide/<uuid:pk>/', views.step5_auto_subdivide, name='step5_auto_subdivide'),
    path('step6/adjustment/<uuid:pk>/', views.step6_adjustment, name='step6_adjustment'),
    path('step7/deliverables/<uuid:pk>/', views.step7_deliverables, name='step7_deliverables'),
    
    # Additional endpoints
    path('download-report/<uuid:pk>/', views.download_report, name='download_report'),
    path('ajax/update-parcel/', views.update_parcel_ajax, name='update_parcel_ajax'),
]