# project/celery.py
import os
from celery import Celery
from django.conf import settings
from celery.schedules import crontab
import logging
# Set Django settings module
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'GGI.settings')

# Initialize Celery app
app = Celery('GGI')
app.conf.timezone = 'Africa/Nairobi'

# Load Celery settings from Django settings.py
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks from installed apps
app.autodiscover_tasks()

# ============================
# INSTANCE ISOLATION
# ============================
# Use the INSTANCE_NAME from settings.py or default to "A"
INSTANCE_NAME = getattr(settings, "INSTANCE_NAME", "A")
logger = logging.getLogger(__name__)
logger.info(f"Celery Worker for instance {INSTANCE_NAME} starting")

# Define unique queue per instance
default_queue = f"celery_{INSTANCE_NAME}"

app.conf.task_default_queue = default_queue
app.conf.task_queues = {
    default_queue: {
        "exchange": default_queue,
        "routing_key": default_queue,
    }
}
app.conf.task_default_routing_key = default_queue

# ============================
# BEAT SCHEDULE
# ============================
# All scheduled tasks automatically go to the instance queue
app.conf.beat_schedule = {
    "create-daily-opening-balance": {
        "task": "apps.accounts.tasks.create_daily_opening_balance",
        "schedule": crontab(minute=5, hour=0),  # daily at 00:05
        "options": {"queue": default_queue},
    },
    "update-sms-delivery-every-5-min": {
        "task": "apps.EasyDocs.tasks.update_sms_delivery_and_balance",
        "schedule": crontab(minute="*/5"),      # every 5 minutes
        "options": {"queue": default_queue},
    },
    "dispatch-due-scheduled-tasks-every-minute": {
        "task": "apps.EasyDocs.tasks.dispatch_due_scheduled_tasks",
        "schedule": crontab(minute="*"),
    },
}

# ============================
# Debug helper
# ============================
@app.task(bind=True)
def debug_task(self):
    print(f"Request: {self.request!r}")

# ============================
# Optional: Automatic task routing for shared_task
# ============================
# If you want all shared_task decorators to default to this instance queue,
# you can set the following in settings.py:
#   CELERY_TASK_DEFAULT_QUEUE = f"celery_{INSTANCE_NAME}"
#   CELERY_TASK_DEFAULT_ROUTING_KEY = f"celery_{INSTANCE_NAME}"
