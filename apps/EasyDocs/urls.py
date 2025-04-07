from django.urls import path
from . import views, services, processes
from .views import ManagementView

urlpatterns = [
    path('', views.home, name='home'),
    path('clients', views.client_list, name='clients'),
    path('clients/add/', views.add_client, name='add_client'),
    path('clients/edit/<int:client_id>/', views.edit_client, name='edit_client'),
    path('add-client-service/', views.add_client_service, name='add_client_service'),
    path('clients/search/', views.search_clients, name='search_clients'),
    path('clients/details/<int:client_id>/', views.client_details, name='client_details'),

    path('services/', services.service_list, name='service_list'),
    path('services/add/', services.add_service, name='add_service'),
    path('services/update/<int:pk>/', services.update_service, name='update_service'),



    path('management/', ManagementView.as_view(), name='management'),

    path('process/<int:pk>/complete/', processes.mark_process_completed, name='mark_process_completed'),
    # Other URLs...
    path('collect-title-deed/<int:service_id>/', processes.collect_title_deed, name='collect_title_deed'),
]


