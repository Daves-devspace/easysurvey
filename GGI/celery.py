# project/celery.py
import os
from celery import Celery


os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'GGI.settings')

app = Celery('GGI')
app.conf.timezone = 'Africa/Nairobi'  # Or your timezone


# Load task modules from all registered Django app configs.
app.config_from_object('django.conf:settings', namespace='CELERY')

app.autodiscover_tasks()


from celery.schedules import crontab

app.conf.beat_schedule = {
    "create-daily-opening-balance": {
        "task": "apps.accounts.tasks.create_daily_opening_balance",
        "schedule": crontab(minute=5, hour=0),  # run every day at 00:05
    },
    
        'update-sms-delivery-every-5-min': {
        'task': 'apps.EasyDocs.tasks.update_sms_delivery_and_balance',
        'schedule': crontab(minute='*/5'),  # every 5 minutes
    }
}


@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
