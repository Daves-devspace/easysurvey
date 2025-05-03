from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from apps.EasyDocs.models import Client, Document, ClientService, ClientDoc
from apps.Employee.models import EmployeeProfile

class Command(BaseCommand):
    help = "Sets up role-based groups and assigns permissions safely"

    def handle(self, *args, **kwargs):
        role_permissions = {
            'Admin': {
                'models': [Client, Document, ClientService, EmployeeProfile],
                'perms': ['add', 'change', 'delete', 'view']
            },
            'Surveyor': {
                'models': [ClientDoc],
                'perms': ['add', 'view', 'change']
            },
            'FrontOffice': {
                'models': [Client, Document, ClientService],
                'perms': ['add', 'view']
            },
        }

        for role, rules in role_permissions.items():
            group, created = Group.objects.get_or_create(name=role)
            if created:
                self.stdout.write(self.style.SUCCESS(f"Created group: {role}"))
            else:
                self.stdout.write(f"Using existing group: {role}")

            if role != "Admin":
                group.permissions.clear()
                self.stdout.write(f"Cleared existing permissions for group: {role}")
            else:
                self.stdout.write(self.style.WARNING("Admin group left untouched for safety"))

            for model in rules['models']:
                content_type = ContentType.objects.get_for_model(model)
                for perm in rules['perms']:
                    codename = f"{perm}_{model._meta.model_name}"
                    try:
                        permission = Permission.objects.get(codename=codename, content_type=content_type)
                        if not group.permissions.filter(id=permission.id).exists():
                            group.permissions.add(permission)
                            self.stdout.write(f" → Added {codename} to {role}")
                    except Permission.DoesNotExist:
                        self.stdout.write(self.style.WARNING(f"Permission not found: {codename}"))

        self.stdout.write(self.style.SUCCESS("✅ All roles and permissions configured successfully."))
