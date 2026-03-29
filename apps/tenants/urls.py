from django.urls import path
from .views import (
    TenantListView,
    TenantOnboardView,
    TenantDetailView,
    TenantUpdateView,
    TenantArchiveView,
    TenantRestoreView,
    SubscriptionSetupView,
    TransactionsListView,
)

urlpatterns = [
    path("subscriptions/", TenantListView.as_view(), name="tenant_list"),
    path("subscriptions/onboard/", TenantOnboardView.as_view(), name="tenant_onboard"),
    path("subscriptions/tenants/<slug:slug>/", TenantDetailView.as_view(), name="tenant_detail"),
    path("subscriptions/tenants/<slug:slug>/update/", TenantUpdateView.as_view(), name="tenant_update"),
    path("subscriptions/tenants/<slug:slug>/archive/", TenantArchiveView.as_view(), name="tenant_archive"),
    path("subscriptions/tenants/<slug:slug>/restore/", TenantRestoreView.as_view(), name="tenant_restore"),
    path("subscriptions/tenants/<slug:slug>/setup/", SubscriptionSetupView.as_view(), name="subscription_setup"),
    path("subscriptions/transactions/", TransactionsListView.as_view(), name="subscription_transactions"),
]
