from django.urls import path
from . import views, auth_views, documents, accounts, reciepts, analytics
from .accounts.legal_payout import BulkPayoutView
from .services import processes, services
from apps.EasyDocs.accounts.accounts import AccountsDashboardView, ExpenseView, SubServiceFilterView, \
    LegalPayoutCreateView

from .auth_views import CustomPasswordResetConfirmView
from .communication import CommunicationView
from .views import ManagementView, ClientDetailView
from django.contrib.auth.views import PasswordResetDoneView, PasswordResetCompleteView

urlpatterns = [
    path('password_reset/', auth_views.CustomPasswordResetView.as_view(), name='password_reset'),
    path(
        'reset-password/done/',
        PasswordResetDoneView.as_view(template_name='application/password_reset_done.html'),
        name='password_reset_done'
    ),
    path(
        'reset/<uidb64>/<token>/',
        CustomPasswordResetConfirmView.as_view(),
        name='password_reset_confirm'
    ),
    path(
        'reset-password/complete/',
        PasswordResetCompleteView.as_view(template_name='application/password_reset_complete.html'),
        name='password_reset_complete'
    ),

    path('login/', auth_views.custom_login, name='login'),
    path('test_404/', auth_views.test_404, name='test_404'),
    path('logout/', auth_views.logout_view, name='logout'),
    path('', views.home, name='home'),

    path('clients', views.client_list, name='clients'),
    path('clients/add/', views.add_client, name='add_client'),
    path('clients/edit/<int:client_id>/', views.edit_client, name='edit_client'),
    path('add-client-service/', views.add_client_service, name='add_client_service'),
    path('client/<int:client_id>/edit_service/', views.edit_client_service, name='edit_client_service'),
    path('clients/search/', views.search_clients, name='search_clients'),
    path('clients/details/<int:client_id>/', ClientDetailView.as_view(), name='client_details'),

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
    # urls.py
    path('clients/<int:client_id>/upload-doc/', documents.upload_client_doc, name='upload_client_doc'),

    path('add-document/', documents.add_document, name='add_document'),

    path('documents/', documents.document_list, name='document_list'),

    # Client detail page

    # AJAX endpoint to get payment context
    # path('client/<int:pk>/payment-context/', accounts.payment_context, name='payment_context'),
    # # Endpoint to actually make a payment
    # path('client-service/<int:cs_id>/pay/', accounts.make_payment, name='make_payment'),
    path('clients/<int:client_id>/add-payment/', accounts.accounts.add_payment_view, name='add_payment'),

    path('get_service_processes/<int:service_id>/', services.get_service_processes, name='get_service_processes'),

    path('services/by-category/', services.services_by_category, name='services_by_category'),

    path('client-service/<int:cs_id>/receipt/', reciepts.download_receipt, name='download_receipt'),

    path('delete_subservice/<int:id>/', services.delete_subservice, name='delete_subservice'),

    # path('clients/details/<int:client_id>/add_subservice/', views.add_or_update_client_subservice,
    #      name='add_or_update_client_subservice'),

    path('settings/update/', views.update_site_settings, name='update_site_settings'),

    path("update-sms-token/", views.update_sms_token, name="update_sms_token"),

    path('send-doc-email/<int:client_id>/<int:doc_id>/', documents.send_doc_email_to_client,
         name='send_doc_email_to_client'),

    path('accounts/', AccountsDashboardView.as_view(), name='accounts_dashboard'),

    path('accounts/subservices/filter/', SubServiceFilterView.as_view(), name='subservice_filter'),
    path('accounts/payout/create/', LegalPayoutCreateView.as_view(), name='legal_payout_create'),

    path('bulk-payout/', BulkPayoutView.as_view(), name='bulk_payout'),

    # Create new expense
    path("submit-expense/", ExpenseView.as_view(), name="submit_expense"),

    path('expenses/<int:pk>/delete/', accounts.accounts.expense_delete, name='expense_delete'),

    path('api/chart-data/', views.chart_data, name='chart-data'),

    path('api/stacked-chart/', views.stacked_service_data, name='stacked_chart_data'),
    path('api/analysis/monthly-services/', analytics.monthly_service_analysis, name='monthly-service-analysis'),

    path('api/available-years/', views.get_years, name='available-years'),
    path('api/services/', analytics.available_services, name='available_services'),

    path('message-logs/', CommunicationView.as_view(), name='communication_bulk'),

]
