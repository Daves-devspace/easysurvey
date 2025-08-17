from django.urls import path
from . import views, documents, accounts, reciepts, analytics, auth_views, bot
from .accounts.legal_payout import BulkPayToLegalView
from .clients.client_views import SendClientSMSView, \
    DeleteClientServiceView, AddClientSubserviceView, EditClientSubserviceView, DeleteClientSubserviceView, \
    ClientServiceManageView
from .services import processes, services
from apps.EasyDocs.accounts.accounts import AccountsDashboardView, ExpenseView, SubServiceFilterView, \
    LegalPayoutCreateView
from apps.EasyDocs.services.sub_services import SubServicesStatusView
from apps.EasyDocs.auth_views import CustomPasswordResetConfirmView, LandingPageView, CustomLoginView, CustomPasswordResetView


from .communication import CommunicationView
from .services.bookings import BookingManagementView, MarkBookingHandledView, AssignSurveyorsView, BookingCalendarJSON
from .services.services import BookingUpdateView
from .views import ManagementView, ClientDetailView, ClientServiceCreateView, HomeView, StaffDashboardView
from django.contrib.auth.views import PasswordResetDoneView, PasswordResetCompleteView

urlpatterns = [
    
    path('api/get-similarity/', bot.get_similarity, name='get_embedding'),
    
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

    path('client/<int:client_id>/delete-doc/<int:doc_id>/', documents.delete_document, name='delete_document'),
    # urls.py
    path('add-doctype/', documents.add_doctype, name='add_doctype'),
    # urls.py
    path('clients/<int:client_id>/upload-doc/', documents.upload_client_doc, name='upload_client_doc'),

    path('add-document/', documents.add_document, name='add_document'),

    path('documents/', documents.document_list, name='document_list'),

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

    path('subservices/', SubServicesStatusView.as_view(), name='subservices_status'),

    path('accounts/subservices/filter/', SubServiceFilterView.as_view(), name='subservice_filter'),
    path('accounts/payout/create/', LegalPayoutCreateView.as_view(), name='legal_payout_create'),

    path('bulk-payout/', BulkPayToLegalView.as_view(), name='bulk_pay_to_legal'),

    # Create new expense
    path("submit-expense/", ExpenseView.as_view(), name="submit_expense"),

    path('expenses/<int:pk>/delete/', accounts.accounts.expense_delete, name='expense_delete'),

    path('api/chart-data/', views.chart_data, name='chart-data'),

    # path('api/stacked-chart/', views.stacked_service_data, name='stacked_chart_data'),
    path('api/analysis/monthly-services/', analytics.monthly_service_analysis, name='monthly-service-analysis'),

    path('api/available-years/', views.get_years, name='available-years'),
    path('api/services/', analytics.available_services, name='available_services'),

    path('message-logs/', CommunicationView.as_view(), name='communication_bulk'),

    path('booking/<int:pk>/edit/', BookingUpdateView.as_view(), name='edit_booking'),

    path('bookings/<int:pk>/mark-handled/', MarkBookingHandledView.as_view(), name='mark-booking-handled'),
    path('bookings/<int:pk>/assign/', AssignSurveyorsView.as_view(), name='assign-surveyors'),
    path('bookings/', BookingManagementView.as_view(), name='booking-management'),
    path('api/calendar/bookings/', BookingCalendarJSON.as_view(), name='booking-calendar-json'),
    
    
    path('projects/', views.projects_view, name='projects')
]
