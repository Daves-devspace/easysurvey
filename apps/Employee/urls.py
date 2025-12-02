from django.urls import path
from . import views
from .admin_views import EmployeeProfileDashboardView, \
    AdminManagementView, UserManagementView
from .salary.salary import EmployeeSalaryCreateView, EmployeeSalaryDeleteView, EmployeeSalaryUpdateView, \
    AllowanceCreateView, AllowanceUpdateView, AllowanceDeleteView, DeductionCreateView, DeductionUpdateView, \
    DeductionDeleteView, EmployeePayrollGenerateView, PayrollMarkPaidView, BulkPayrollMarkPaidView
from .views import PayrollGenerateAllView

urlpatterns = [

    # Employee URLs
    path('employees/', views.EmployeeListView.as_view(), name='employee_list'),
    path('employees/add/', views.EmployeeCreateView.as_view(), name='employee_add'),
    path('employees/edit/<int:pk>/', views.EmployeeUpdateView.as_view(), name='employee_edit'),

    path('employee/<int:employee_pk>/salary/add/',
         EmployeeSalaryCreateView.as_view(),
         name='salary_create'),

    path('salary/<int:pk>/edit/',
         EmployeeSalaryUpdateView.as_view(),
         name='salary_update'),

    path('salary/<int:pk>/delete/',
         EmployeeSalaryDeleteView.as_view(),
         name='salary_delete'),

    path('employee/<int:employee_id>/allowance/add/', AllowanceCreateView.as_view(), name='allowance_create'),
    path('allowance/<int:pk>/edit/', AllowanceUpdateView.as_view(), name='allowance_edit'),
    path('allowance/<int:pk>/delete/', AllowanceDeleteView.as_view(), name='allowance_delete'),

    path('employee/<int:employee_id>/deduction/add/', DeductionCreateView.as_view(), name='deduction_create'),
    path('deduction/<int:pk>/edit/', DeductionUpdateView.as_view(), name='deduction_edit'),
    path('deduction/<int:pk>/delete/', DeductionDeleteView.as_view(), name='deduction_delete'),
    path('filters/', views.filter_payrolls, name='filter_payrolls'),
    # … your other patterns …
    path(
        'payroll/generate/all/',
        PayrollGenerateAllView.as_view(),  # ← use .as_view()
        name='payroll_generate_all'
    ),
    path(
        'payroll/generate/<int:employee_id>/',
        EmployeePayrollGenerateView.as_view(),  # ← use .as_view()
        name='payroll_generate_one'
    ),

    path(
        'payroll/<int:pk>/mark-paid/',
        PayrollMarkPaidView.as_view(),
        name='payroll_mark_paid'
    ),
    path(
        'payroll/payment/',
       BulkPayrollMarkPaidView.as_view(),
        name='payroll_bulk_mark_paid'
    ),
    path('dashboard/', EmployeeProfileDashboardView.as_view(), name='employee-dashboard'),
    path('profile/user/', AdminManagementView.as_view(), name='user-profile-update'),
    path('manage/user/', UserManagementView.as_view(), name='users-update'),

    

]
