
# urls.py
from django.urls import path
from .views import (
    PropertyDetailView,
    PropertyListView,
    PropertyCreateView,
    PropertyUpdateView,
    PropertyDeleteView,
    
)
from apps.tenant_management.units.units import UnitListView, UnitCreateView, UnitUpdateView, UnitDeleteView
from apps.tenant_management.lease.lease import  TenantLeaseCreateView,LeaseListView, LeaseDetailView, end_lease_view,get_units_by_property,LeaseCreateView,LeaseUpdateView,LeaseDeleteView
from apps.tenant_management.tenants.tenant import TenantCreateView, TenantListView, TenantDetailView, TenantUpdateView, TenantDeleteView

urlpatterns = [
     path("properties/", PropertyListView.as_view(), name="property-list"),
    path("properties/create/", PropertyCreateView.as_view(), name="property-create"),
    path("properties/<int:pk>/edit/", PropertyUpdateView.as_view(), name="property-edit"),
    path('delete/<int:pk>/', PropertyDeleteView.as_view(), name='property-delete'),
    
    path('properties/<int:pk>/', PropertyDetailView.as_view(), name='property_detail'),
    path('properties/<int:pk>/units/', UnitListView.as_view(), name='unit_list'),
    path('properties/<int:pk>/units/add/', UnitCreateView.as_view(), name='unit_add'),
    path('properties/<int:pk>/units/<int:unit_pk>/edit/', UnitUpdateView.as_view(), name='unit_edit'),
    path('properties/<int:pk>/units/<int:unit_pk>/delete/', UnitDeleteView.as_view(), name='unit_delete'),
    
    
    
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
     name='existing_tenant_lease_create'
     ),
     
     path('units/lease/<int:pk>/edit/',
          LeaseUpdateView.as_view(),
          name='lease_edit'),
     
     

    # Lease management URLs
    path('leases/',
         LeaseListView.as_view(),
         name='lease_list'),  # List all leases, with filters

    path('leases/<int:pk>/',
         LeaseDetailView.as_view(),
         name='lease_detail'),  # Detailed view of a single lease

    path('leases/<int:lease_id>/end/',
         end_lease_view,
         name='end_lease'),  # POST endpoint to end a lease and free its unit

    # AJAX endpoint for dynamic dropdown of available units
    path('api/units-by-property/',
         get_units_by_property,
         name='units_by_property'),
]

