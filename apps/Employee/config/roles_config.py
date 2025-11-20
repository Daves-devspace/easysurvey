# yourapp/config/roles_config.py
from apps.EasyDocs.models import Client, ClientService, Document, Payment, ClientSubService, SubService, Service, \
    Process, Booking, BookingAssignment, ClientServiceProcess, TitleDeedCollection, PaymentHistory, Expense, \
    LegalOfficePayout, ClientDoc, DocType, MessageLog, SmsProviderToken, SiteSettings
from apps.Employee.models import EmployeeProfile, EmployeeSalary, AllowanceTemplate, DeductionTemplate, \
    AllowanceSnapshot, DeductionSnapshot, Payroll

ROLE_PERMISSIONS = {
    EmployeeProfile.RoleChoices.ADMIN: {
        'permissions': {
            Client: ['add', 'change', 'delete', 'view'],
            ClientService: ['add', 'change', 'delete', 'view'],
            ClientSubService: ['add', 'change', 'delete', 'view'],
            SubService: ['add', 'change', 'delete', 'view'],
            Service: ['add', 'change', 'delete', 'view'],
            Process: ['add', 'change', 'delete', 'view'],
            Booking: ['add', 'change', 'delete', 'view'],
            BookingAssignment: ['add', 'change', 'delete', 'view'],
            ClientServiceProcess: ['add', 'change', 'delete', 'view'],
            TitleDeedCollection: ['add', 'change', 'delete', 'view'],
            Payment: ['add', 'change', 'delete', 'view'],
            PaymentHistory: ['add', 'change', 'delete', 'view'],
            Expense: ['add', 'change', 'delete', 'view'],
            LegalOfficePayout: ['add', 'change', 'delete', 'view'],
            EmployeeProfile: ['add', 'change', 'delete', 'view'],
            EmployeeSalary: ['add', 'change', 'delete', 'view'],
            AllowanceTemplate: ['add', 'change', 'delete', 'view'],
            DeductionTemplate: ['add', 'change', 'delete', 'view'],
            DeductionSnapshot: ['add', 'change', 'delete', 'view'],
            AllowanceSnapshot: ['add', 'change', 'delete', 'view'],
            Payroll: ['add', 'change', 'delete', 'view'],
            ClientDoc: ['add', 'change', 'delete', 'view'],
            Document: ['add', 'change', 'delete', 'view'],
            DocType: ['add', 'change', 'delete', 'view'],
            MessageLog: ['add', 'change', 'delete', 'view'],
            SmsProviderToken: ['change', 'view'],  # typically one row, so no 'add' or 'delete'
            SiteSettings: ['change', 'view'],  # typically one row, so no 'add' or 'delete'

        }
    },

    EmployeeProfile.RoleChoices.SURVEYOR: {
        'permissions': {
            ClientService: ['view','change','add'],  # can add/view only
            Client: ['view'],
            Payment: ['add', 'view'],
            TitleDeedCollection: ['view'],  # no delete
            ClientDoc: ['add', 'change', 'view', 'delete'],  # no delete
            MessageLog: ['view'],
            Document: ['add', 'view'],  # ❌ can't delete/change office documents
        }
    },

    EmployeeProfile.RoleChoices.FRONTOFFICE: {
        'permissions': {
            Client: ['add', 'view', 'change'],
            ClientService: ['add', 'view', 'change'],
            ClientSubService: ['add', 'change', 'view'],
            ClientServiceProcess: ['add', 'change','view'],
            Expense: ['add', 'change','view'],
            TitleDeedCollection: ['add', 'view'],
            MessageLog: ['add', 'change', 'view'],
            ClientDoc: ['add', 'view'],
            Payment: ['add', 'view'],
            Document: ['add', 'view'],  # e.g., receptionist uploads scans
        }
    },
}
