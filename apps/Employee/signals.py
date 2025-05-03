# signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import Group
from .models import EmployeeProfile

@receiver(post_save, sender=EmployeeProfile)
def assign_group_based_on_role(sender, instance, created, **kwargs):
    if created:
        role = instance.role
        user = instance.user
        if role:
            group, _ = Group.objects.get_or_create(name=role)
            user.groups.add(group)
