from django.urls import path
from . import views, documents, accounts, reciepts, analytics, auth_views, bot_views,audit
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


from .communication import CommunicationView
from .services.bookings import BookingManagementView, MarkBookingHandledView, AssignSurveyorsView, BookingCalendarJSON
from .services.bookings import BookingUpdateView, BookingCreateView
from .views import ManagementView, ClientDetailView, ClientServiceCreateView, HomeView, StaffDashboardView, TaskManagementView
from django.contrib.auth.views import PasswordResetDoneView, PasswordResetCompleteView
from apps.EasyDocs.files.oauth import  drive_oauth_start, drive_oauth_callback,RefreshDriveTokenView
from apps.notifications.views import firebase_messaging_sw


urlpatterns = [
    
    path('firebase-messaging-sw.js', firebase_messaging_sw, name='firebase_sw'),
    
        # Main query endpoint - handles all bot queries
    path('api/bot/query/', bot_views.bot_query, name='bot_query'),
    
    # Health check - verify bot is working
    path('api/bot/health/', bot_views.bot_health, name='bot_health'),
    
    # Clear session - reset conversation history
    path('api/bot/clear-session/', bot_views.clear_session, name='bot_clear_session'),
    
    # Get conversation history (optional)
    path('api/bot/history/', bot_views.get_conversation_history, name='bot_history'),  
    # Clear cache - refresh all cached responses
    path('api/bot/clear-cache/', bot_views.clear_cache, name='bot_clear_cache'),
    
    
    
    path("drive/oauth/start/", drive_oauth_start, name="drive_oauth_start"),
    path("drive/oauth/callback/", drive_oauth_callback, name="drive_oauth_callback"), 
    
    
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
    path('tasks/', TaskManagementView.as_view(), name='task-management'),

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
    
     path('clients/<int:client_id>/migrate-to-drive/', 
         file_views.migrate_client_documents_to_drive, 
         name='migrate_client_docs_to_drive'),
    
    path('documents/migrate-all-to-drive/', 
         file_views.migrate_all_documents_to_drive, 
         name='migrate_all_docs_to_drive'),

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
    path('payments/<int:payment_id>/adjust/', accounts.adjust_payment_view, name='adjust_payment'),
    path('subservices/<int:sub_service_id>/adjust/', accounts.adjust_subservice_payment_view, name='adjust_subservice_payment'),

    path('get_service_processes/<int:service_id>/', services.get_service_processes, name='get_service_processes'),

    path('services/by-category/', services.services_by_category, name='services_by_category'),

    path('client-service/<int:cs_id>/receipt/', reciepts.download_receipt, name='download_receipt'),

    path('delete_subservice/<int:id>/', services.delete_subservice, name='delete_subservice'),

    # path('clients/details/<int:client_id>/add_subservice/', views.add_or_update_client_subservice,
    #      name='add_or_update_client_subservice'),

    path('settings/update/', views.update_site_settings, name='update_site_settings'),
    path('settings/process-notifications/', views.update_process_notification_settings, name='update_process_notifications'),
    
    # Service assignment accept/decline endpoints
    path('assignments/<int:client_service_id>/accept/', views.accept_service_assignment, name='accept_service_assignment'),
    path('assignments/<int:client_service_id>/decline/', views.decline_service_assignment, name='decline_service_assignment'),
    path('assignments/<int:client_service_id>/extend-deadline/', views.request_deadline_extension, name='request_deadline_extension'),
    
    # Document handoff accept/decline endpoints
    path('handoffs/assign/', file_views.assign_document_handoff, name='assign_document_handoff'),
    path('handoffs/<int:handoff_id>/accept/', views.accept_document_handoff, name='accept_document_handoff'),
    path('handoffs/<int:handoff_id>/decline/', views.decline_document_handoff, name='decline_document_handoff'),

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
    
    
    
    
    path('google-drive/config/', connection.google_drive_deployment_config, name='google_drive_config'),
    path('google-drive/config/update/', connection.google_drive_config_update_ajax, name='google_drive_config_update_ajax'),
    path('google-drive/config/clear-key/', connection.google_drive_config_clear_key, name='google_drive_config_clear_key'),
    path('google-drive/test-connection/', connection.test_google_drive_connection, name='test_google_drive_connection'),
    path('test-connection/', connection.test_google_drive_connection, name='test_connection'),
    path('generate-key/', connection.generate_deployment_key, name='generate_key'),
    path('debug-drive-config/', connection.debug_drive_config, name='debug_drive_config'),
    path('emergency-share-folder/', connection.emergency_share_folder, name='emergency_share_folder'),
    path(
        "oauth/refresh-token/",
        RefreshDriveTokenView.as_view(),
        name="refresh_drive_token",
    ),
    
    path('system/audit-logs/',audit.AuditLogListView.as_view(),name='system_audit_logs'),
    
    path('sessions/', views.sessions, name='sessions'),                 # list & search survey sessions
    path('files/upload/', views.file_upload, name='file_uploads'),      # upload CSV/DXF files
    path('map/', views.map_viewer, name='map_view'),                    # map viewer (search + render)
    path('mutation/', views.mutation_tool, name='mutation_view'),       # subdivision / mutation UI
    path('mutation/export/', views.mutation_export, name='mutation_export'),  # exports & downloads

    
    
     
    
    
    

]