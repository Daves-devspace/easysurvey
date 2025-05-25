from django.apps import AppConfig
from django.db.models.signals import post_migrate


class EasyDocsConfig(AppConfig):
    name = 'apps.EasyDocs'
    label = 'easydocs'

    def ready(self):
        import apps.EasyDocs.signals  # register signals safely here

        # Import here, so it's after apps are loaded
        from django_celery_beat.models import PeriodicTask, CrontabSchedule

        def create_periodic_task(sender, **kwargs):
            schedule, _ = CrontabSchedule.objects.get_or_create(
                minute='0',
                hour='7',
                day_of_week='*',
                day_of_month='*',
                month_of_year='*',
                timezone='Africa/Nairobi'
            )
            PeriodicTask.objects.get_or_create(
                crontab=schedule,
                name='Send Ground Reminders',
                task='apps.EasyDocs.tasks.send_today_ground_reminders',
            )

        # Connect to post_migrate to run this after migrations have run
        post_migrate.connect(create_periodic_task, sender=self)
