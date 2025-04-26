from django.urls import path
from .views import EmployeeManagementView

urlpatterns = [
    path('employees/', EmployeeManagementView.as_view(), name='employee_management'),
]
