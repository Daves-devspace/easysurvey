from django.urls import path
from .views import CashbookDashboardView, RecordInstitutionPayoutView, OpeningBalanceCreateView, CheckOpeningBalanceView, SyncOpeningBalanceView, CheckOpeningSyncView, RevenueReportView
from .reciept import DailyCashbookPDFView
from .services import revenue_pdf as pdf_views

urlpatterns = [
    path("cashbook/", CashbookDashboardView.as_view(), name="cashbook_dashboard"),
    path("external/payout/create/", RecordInstitutionPayoutView.as_view(), name="record-institution-payout"),
    path("api/opening-balance/create/", OpeningBalanceCreateView.as_view(), name="opening-balance"),
    path("api/check-opening-balance/", CheckOpeningBalanceView.as_view(), name="check-opening-balance"),
    path("api/check-opening-sync/", CheckOpeningSyncView.as_view(), name="check_opening_sync"),
    path("sync-opening-balance/", SyncOpeningBalanceView.as_view(), name="sync_opening_balance"),
    path("daily-cashbook-pdf/", DailyCashbookPDFView.as_view(), name="daily_cashbook_pdf"),
    path('revenue/pdf/', pdf_views.revenue_pdf_view, name='revenue_pdf'),
    path('revenue/excel/', pdf_views.revenue_excel_view, name='revenue_excel'),

    path('revenue', RevenueReportView.as_view(), name='revenue-report'),    
    
    
 
]
