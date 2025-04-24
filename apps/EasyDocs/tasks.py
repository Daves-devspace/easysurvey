# core/tasks.py
from datetime import datetime

from celery import shared_task

from apps.EasyDocs.models import Client
from apps.EasyDocs.utils import update_pending_sms_logs_and_balance, send_single_sms

@shared_task
def update_sms_delivery_and_balance():
    print("Running scheduled SMS delivery and balance update")
    update_pending_sms_logs_and_balance()


@shared_task
def schedule_single_sms(client_id, template, scheduled_time):
    client = Client.objects.get(id=client_id)
    text = personalize(template, client)
    # If time has arrived, send immediately
    if datetime.fromisoformat(scheduled_time) <= datetime.now():
        return send_single_sms(client, text, reason="Scheduled SMS")
    # Otherwise re-schedule this very task at the correct ETA
    return schedule_single_sms.apply_async(
        args=(client_id, template, scheduled_time),
        eta=datetime.fromisoformat(scheduled_time),
    )

@shared_task
def schedule_bulk_personalized_sms(template, scheduled_time=None):
    clients = Client.objects.all()
    for client in clients:
        text = personalize(template, client)
        if scheduled_time:
            # schedule each individually
            schedule_single_sms.apply_async(
                args=(client.id, template, scheduled_time),
                eta=datetime.fromisoformat(scheduled_time),
            )
        else:
            # send now
            send_single_sms(client, text, reason="Bulk SMS")
    return {"count": clients.count()}