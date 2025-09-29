from django.urls import path
from . import views, documents, accounts, reciepts, analytics, auth_views, bot
from apps.EasyDocs.files import views as file_views
from apps.EasyDocs.files import connection
from apps.EasyDocs.accounts import accounts
from .accounts.legal_payout import BulkPayToLegalView
from .clients.client_views import SendClientSMSView, \
    DeleteClientServiceView, AddClientSubserviceView, EditClientSubserviceView, DeleteClientSubserviceView, \
    ClientServiceManageView, soft_delete_client_subservice,restore_client_subservice, hard_delete_client_subservice

from .services import processes, services
from apps.EasyDocs.accounts.accounts import AccountsDashboardView, ExpenseView, SubServiceFilterView, \
    LegalPayoutCreateView
from apps.EasyDocs.services.sub_services import SubServicesStatusView
from apps.EasyDocs.auth_views import CustomPasswordResetConfirmView, LandingPageView, CustomLoginView, CustomPasswordResetView

from apps.EasyDocs.bot import views_enqueue, views_result, views_async, views_kb
from .communication import CommunicationView
from .services.bookings import BookingManagementView, MarkBookingHandledView, AssignSurveyorsView, BookingCalendarJSON
from .services.bookings import BookingUpdateView, BookingCreateView
from .views import ManagementView, ClientDetailView, ClientServiceCreateView, HomeView, StaffDashboardView
from django.contrib.auth.views import PasswordResetDoneView, PasswordResetCompleteView

from apps.EasyDocs.files.oauth import  drive_oauth_start, drive_oauth_callback

urlpatterns = [
    
    #path('api/bot/forward/', bot.forward_to_n8n, name='bot_forward'),
    path("drive/oauth/start/", drive_oauth_start, name="drive_oauth_start"),
    path("drive/oauth/callback/", drive_oauth_callback, name="drive_oauth_callback"), 
    
    path("api/kb/", views_kb.knowledge_base, name="knowledge_base"),
        # Enqueue request → Celery task, returns 202 + poll_url
    path("api/bot/enqueue/", views_enqueue.enqueue_forward, name="bot-enqueue"),

    # Poll for result by request_id (returns 200 ready, 202 pending, 404 not found)
    path("api/bot/result/<uuid:request_id>/", views_result.poll_result, name="bot-result"),
    
    path("api/bot/result/<uuid:request_id>/complete/", views_result.store_result, name="bot-result-complete"),


    # Direct async forward (bypasses queue, runs via httpx.AsyncClient)
    path("forward/", views_async.forward_to_n8n_async, name="bot-forward-async"),
    
    path('password-reset/', CustomPasswordResetView.as_view(), name='password_reset'),
    path(
        'password-reset/done/',
        PasswordResetDoneView.as_view(template_name='application/password_reset_done.html'),
        name='password_reset_done'
    ),
    path(
        'password-reset-confirm/<uidb64>/<token>/',
        CustomPasswordResetConfirmView.as_view(),
        name='password_reset_confirm'
    ),
    path(
        'password-reset/complete/',
        PasswordResetCompleteView.as_view(template_name='application/password_reset_complete.html'),
        name='password_reset_complete'
    ),

    path('', LandingPageView.as_view(), name='landing'),
    path('login/', CustomLoginView.as_view(), name='login'),

    path('logout/', auth_views.logout_view, name='logout'),
    path('admin-dashboard/', HomeView.as_view(), name='home'),
    path('staff-dashboard/', StaffDashboardView.as_view(), name='staff-dashboard'),

    path('clients', views.client_list, name='clients'),
    path('clients/add/', views.add_client, name='add_client'),
    path('clients/edit/<int:client_id>/', views.edit_client, name='edit_client'),
    path('add-client-service/', ClientServiceCreateView.as_view(), name='add_client_service'),
    path('client/<int:client_id>/edit_service/', views.edit_client_service, name='edit_client_service'),
    path('clients/search/', views.search_clients, name='search_clients'),
    path('clients/details/<int:client_id>/', ClientDetailView.as_view(), name='client_details'),


    path('management/', ManagementView.as_view(), name='management'),

    path('process/<int:pk>/complete/', processes.mark_process_completed, name='mark_process_completed'),
    # Other URLs...
    path('collect-title-deed/<int:service_id>/', processes.collect_title_deed, name='collect_title_deed'),

    path('client/<int:client_id>/delete-doc/<int:doc_id>/', file_views.delete_client_document, name='delete_document'),
    # urls.py
    path('add-doctype/', file_views.add_doctype, name='add_doctype'),
    # urls.py
    path('clients/<int:client_id>/upload-doc/', file_views.upload_client_document, name='upload_client_doc'),

    path('add-document/', file_views.upload_office_document, name='add_document'),

    path('documents/', file_views.office_documents, name='document_list'),

    # Client detail page

    path('clients/<int:client_id>/sms/', SendClientSMSView.as_view(), name='send_client_sms'),
    path('clients/<int:client_id>/services/', ClientServiceManageView.as_view(), name='client-service'),
    # path('clients/<int:client_id>/services/edit/', EditClientServiceView.as_view(), name='client-edit-service'),
    path('clients/<int:client_id>/services/delete/', DeleteClientServiceView.as_view(), name='client-delete-service'),
    path('clients/<int:client_id>/subservices/add/', AddClientSubserviceView.as_view(), name='client-add-subservice'),
    path('clients/<int:client_id>/subservices/edit/', EditClientSubserviceView.as_view(),
         name='client-edit-subservice'),
    path('clients/<int:client_id>/subservices/delete/', DeleteClientSubserviceView.as_view(),
         name='client-delete-subservice'),
    
    
    path('client-subservice/<int:pk>/soft-delete/', soft_delete_client_subservice, name='soft_delete_client_subservice'),
    path('client-subservice/<int:pk>/restore/', restore_client_subservice, name='restore_client_subservice'),
    path('client-subservice/<int:pk>/hard-delete/', hard_delete_client_subservice, name='hard_delete_client_subservice'),

    path('clients/<int:client_id>/add-payment/', accounts.add_payment_view, name='add_payment'),

    path('get_service_processes/<int:service_id>/', services.get_service_processes, name='get_service_processes'),

    path('services/by-category/', services.services_by_category, name='services_by_category'),

    path('client-service/<int:cs_id>/receipt/', reciepts.download_receipt, name='download_receipt'),

    path('delete_subservice/<int:id>/', services.delete_subservice, name='delete_subservice'),

    # path('clients/details/<int:client_id>/add_subservice/', views.add_or_update_client_subservice,
    #      name='add_or_update_client_subservice'),

    path('settings/update/', views.update_site_settings, name='update_site_settings'),

    path("update-sms-token/", views.update_sms_token, name="update_sms_token"),

    path('client/<int:client_id>/docs/<int:doc_id>/email/', file_views.email_client_document,
         name='send_doc_email_to_client'),

    path('accounts/', AccountsDashboardView.as_view(), name='accounts_dashboard'),

    path('subservices/', SubServicesStatusView.as_view(), name='subservices_status'),

    path('accounts/subservices/filter/', SubServiceFilterView.as_view(), name='subservice_filter'),
    path('accounts/payout/create/', LegalPayoutCreateView.as_view(), name='legal_payout_create'),

    path('bulk-payout/', BulkPayToLegalView.as_view(), name='bulk_pay_to_legal'),

    # Create new expense
    path("submit-expense/", ExpenseView.as_view(), name="submit_expense"),

    path('expenses/<int:pk>/delete/', accounts.expense_delete, name='expense_delete'),

    path('api/chart-data/', views.chart_data, name='chart-data'),

    # path('api/stacked-chart/', views.stacked_service_data, name='stacked_chart_data'),
    path('api/analysis/monthly-services/', analytics.monthly_service_analysis, name='monthly-service-analysis'),

    path('api/available-years/', views.get_years, name='available-years'),
    path('api/services/', analytics.available_services, name='available_services'),

    path('message-logs/', CommunicationView.as_view(), name='communication_bulk'),

    path('booking/<int:pk>/edit/', BookingUpdateView.as_view(), name='edit_booking'),
    path('clients/<int:client_service_id>/bookings/add/', BookingCreateView.as_view(), name='booking_create'),


    path('bookings/<int:pk>/mark-handled/', MarkBookingHandledView.as_view(), name='mark-booking-handled'),
    path('bookings/<int:pk>/assign/', AssignSurveyorsView.as_view(), name='assign-surveyors'),
    path('bookings/', BookingManagementView.as_view(), name='booking-management'),
    path('api/calendar/bookings/', BookingCalendarJSON.as_view(), name='booking-calendar-json'),
    
    
    path('projects/', views.projects_view, name='projects'),
    
    path('google-drive/config/', connection.google_drive_deployment_config, name='google_drive_config'),
    path('google-drive/config/update/', connection.google_drive_config_update_ajax, name='google_drive_config_update_ajax'),
    path('google-drive/config/clear-key/', connection.google_drive_config_clear_key, name='google_drive_config_clear_key'),
    path('google-drive/test-connection/', connection.test_google_drive_connection, name='test_google_drive_connection'),
    path('test-connection/', connection.test_google_drive_connection, name='test_connection'),
    path('generate-key/', connection.generate_deployment_key, name='generate_key'),
    path('debug-drive-config/', connection.debug_drive_config, name='debug_drive_config'),
    path('emergency-share-folder/', connection.emergency_share_folder, name='emergency_share_folder'),
    

]