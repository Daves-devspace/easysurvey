
# urls.py
from django.urls import path

from . import views
from apps.tenant_management.dashboard.kpis import (
    financial_kpis,
    occupancy_kpis,
    operational_kpis,
    collections_kpis,
)
from .views import (
    PropertyDetailView,
    PropertyListView,
    PropertyCreateView,
    PropertyUpdateView,
    PropertyDeleteView,
    PropertyReadingsPartialView,
    PropertyPaymentsPartialView,
    
)
from apps.tenant_management.tenants.payments import TenantPaymentModalView,ManualInvoiceGenerationView
from apps.tenant_management.lease.meter_readings import MeterReadingListView, MeterReadingDeleteView, MeterReadingCreateView, MeterReadingUpdateView
from apps.tenant_management.units.units import UnitListView, UnitCreateView, UnitUpdateView, UnitDeleteView
from apps.tenant_management.lease.lease import  TenantLeaseCreateView,LeaseListView, LeaseDetailView,get_units_by_property,LeaseCreateView,LeaseUpdateView,LeaseDeleteView
from apps.tenant_management.tenants.tenant import TenantCreateView, TenantListView, TenantDetailView, TenantUpdateView, TenantDeleteView, TenantInvoicesFilterView, TenantMeterReadingsFilterView

urlpatterns = [
     path("properties/", PropertyListView.as_view(), name="property-list"),
    path("properties/create/", PropertyCreateView.as_view(), name="property-create"),
    path("properties/<int:pk>/edit/", PropertyUpdateView.as_view(), name="property-edit"),
    path('delete/<int:pk>/', PropertyDeleteView.as_view(), name='property-delete'),
    
    path('properties/<int:pk>/', PropertyDetailView.as_view(), name='property_detail'),
    path('properties/<int:pk>/payments-filter/', PropertyPaymentsPartialView.as_view(), name='property_payments_filter'),
    path("properties/<int:pk>/readings/", PropertyReadingsPartialView.as_view(), name="property_readings_partial"),
    
    path('properties/<int:pk>/units/', UnitListView.as_view(), name='unit_list'),
    path('properties/<int:pk>/units/add/', UnitCreateView.as_view(), name='unit_add'),
    path('properties/<int:pk>/units/<int:unit_pk>/edit/', UnitUpdateView.as_view(), name='unit_edit'),
    path('properties/<int:pk>/units/<int:unit_pk>/delete/', UnitDeleteView.as_view(), name='unit_delete'),
    
    
    path("<int:unit_id>/", MeterReadingListView.as_view(), name="list"),
    path("<int:unit_id>/new/", MeterReadingCreateView.as_view(), name="meter_readings_add"),
    path("<int:pk>/edit/", MeterReadingUpdateView.as_view(), name="meter_readings_update"),
    path("<int:pk>/delete/", MeterReadingDeleteView.as_view(), name="delete"),
    
    path('tenant/<int:tenant_id>/payment/', TenantPaymentModalView.as_view(), name='tenant_payment_modal'),
    path(
        'tenants/<int:tenant_id>/invoices/filter/',
        TenantInvoicesFilterView.as_view(),
        name='tenant_invoices_filter'
    ),
      path('tenants/<int:tenant_id>/meter-readings/filter/', 
         TenantMeterReadingsFilterView.as_view(), 
         name='tenant_meter_readings_filter'),
    
     path(
        "invoices/manual-generate/",
        ManualInvoiceGenerationView.as_view(),
        name="manual_invoice_generate",
    ),
     
    
    # Tenant management URLs
     path('tenants/<int:tenant_id>/',
           TenantDetailView.as_view(),
           name='tenant_detail'),  # Detailed view of a single tenant
    
    path('tenants/',
         TenantListView.as_view(),
         name='tenant_list'),  # List all tenants
    
    path("properties/<int:property_id>/tenants/<int:pk>/edit/", 
         TenantUpdateView.as_view(), name="tenant_edit"),
    
    path("properties/<int:property_id>/tenants/<int:pk>/delete/", 
         TenantDeleteView.as_view(), name="tenant_delete"),

    path(
     "properties/<int:property_id>/tenants/create/",
     TenantCreateView.as_view(),
     name="tenant_create",
     ),
     # Form to add a new tenant

    # Combined Tenant + Lease creation
#     path(
#         'tenants/create-with-lease/<int:unit_id>/',
#         TenantLeaseCreateView.as_view(),
#         name='tenant_lease_create'
#     ), # Single workflow to create tenant and lease
    
      path(
      'properties/<int:property_id>/units/<int:unit_id>/tenant-lease/',
      TenantLeaseCreateView.as_view(),
      name='tenant_lease_create'
    ),
     path(
     'units/<int:unit_id>/tenant/<int:tenant_id>/existing-tenant-lease/',
     LeaseCreateView.as_view(),
     name='lease_create'
     ),
     
     path('units/lease/<int:pk>/edit/',
          LeaseUpdateView.as_view(),
          name='lease_edit'),
     
     path('api/units/search/', views.unit_search_json, name='unit_search_json'),
     
     

    # Lease management URLs
    path('leases/',
         LeaseListView.as_view(),
         name='lease_list'),  # List all leases, with filters

    path('leases/<int:pk>/',
         LeaseDetailView.as_view(),
         name='lease_detail'),  # Detailed view of a single lease

#     path('leases/<int:lease_id>/end/',
#          end_lease_view,
#          name='end_lease'),  # POST endpoint to end a lease and free its unit

    # AJAX endpoint for dynamic dropdown of available units
    path('api/units-by-property/',
         get_units_by_property,
         name='units_by_property'),
    
    
    
    path("api/dashboard/financial/", financial_kpis),
    path("api/dashboard/occupancy/", occupancy_kpis),
    path("api/dashboard/operational/", operational_kpis),
    path("api/dashboard/collections/", collections_kpis),
    
    

]

