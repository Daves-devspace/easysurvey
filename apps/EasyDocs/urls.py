from django.urls import path
from . import views, services, processes, auth_views, documents, accounts, reciepts
from .views import ManagementView

urlpatterns = [
    path('login/', auth_views.custom_login, name='login'),
    path('logout/', auth_views.logout_view, name='logout'),
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

    path('client/<int:client_id>/delete-doc/<int:doc_id>/', documents.delete_document, name='delete_document'),
    # urls.py
    path('add-doctype/', documents.add_doctype, name='add_doctype'),

    # Client detail page

    # AJAX endpoint to get payment context
    path('client/<int:pk>/payment-context/', accounts.payment_context, name='payment_context'),
    # Endpoint to actually make a payment
    path('client-service/<int:cs_id>/pay/', accounts.make_payment, name='make_payment'),
    path('clients/<int:client_id>/add-payment/', accounts.add_payment_view, name='add_payment'),

    path('client-service/<int:cs_id>/receipt/', reciepts.download_receipt, name='download_receipt'),

]
