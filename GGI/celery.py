# project/celery.py
import os
from celery import Celery


os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'GGI.settings')

app = Celery('GGI')
app.conf.timezone = 'Africa/Nairobi'  # Or your timezone


# Load task modules from all registered Django app configs.
app.config_from_object('django.conf:settings', namespace='CELERY')

app.autodiscover_tasks()

@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
