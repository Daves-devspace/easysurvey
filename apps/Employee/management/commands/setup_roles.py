# yourapp/management/commands/setup_roles.py

from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType

from apps.Employee.config.roles_config import ROLE_PERMISSIONS


class Command(BaseCommand):
    help = 'Sets up default roles and permissions from roles_config.py'

    def handle(self, *args, **options):
        for role, config in ROLE_PERMISSIONS.items():
            group, created = Group.objects.get_or_create(name=role)
            if created:
                self.stdout.write(self.style.SUCCESS(f'Created group: {role}'))

            for model, perms in config['permissions'].items():
                ct = ContentType.objects.get_for_model(model)
                for perm in perms:
                    codename = f"{perm}_{model._meta.model_name}"
                    try:
                        permission = Permission.objects.get(codename=codename, content_type=ct)
                        group.permissions.add(permission)
                        self.stdout.write(self.style.SUCCESS(f'Assigned {codename} to {role}'))
                    except Permission.DoesNotExist:
                        self.stdout.write(self.style.WARNING(f'Permission {codename} not found for {model}'))

        self.stdout.write(self.style.SUCCESS('All roles and permissions have been set up.'))
