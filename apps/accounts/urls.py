from django.urls import path
from .views import CashbookDashboardView, RecordInstitutionPayoutView, OpeningBalanceCreateView, CheckOpeningBalanceView, SyncOpeningBalanceView, CheckOpeningSyncView

urlpatterns = [
    path("cashbook/", CashbookDashboardView.as_view(), name="cashbook_dashboard"),
    path("payout/create/", RecordInstitutionPayoutView.as_view(), name="record-institution-payout"),
    path("api/opening-balance/create/", OpeningBalanceCreateView.as_view(), name="opening-balance"),
    path("api/check-opening-balance/", CheckOpeningBalanceView.as_view(), name="check-opening-balance"),
    path("api/check-opening-sync/", CheckOpeningSyncView.as_view(), name="check_opening_sync"),
    path("sync-opening-balance/", SyncOpeningBalanceView.as_view(), name="sync_opening_balance"),
    
 
]
