from celery import shared_task
from django.core.cache import cache
from django.core.mail import send_mail, EmailMultiAlternatives
from django.conf import settings
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone as dj_timezone
from datetime import datetime, timedelta, timezone as dt_timezone
import logging

from apps.EasyDocs.models import Booking
from apps.notifications.models import PendingPushNotification
from .utils import send_push_to_user

from .firebase_manager import initialize_firebase # Import the manager

logger = logging.getLogger(__name__)

PENDING_FLUSH_LOCK_TIMEOUT = 300

@shared_task
def send_pending_push_notifications(user_id):
    """
    Find pending unsent notifications for a user and try to deliver them.
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()

    # 1. Ensure Firebase is initialized in this worker process
    if not initialize_firebase():
        logger.error("Cannot send pending push: Firebase not configured.")
        return

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.error("send_pending_push_notifications: user %s does not exist", user_id)
        return

    flush_lock_key = f"notifications:pending-flush:lock:{user_id}"
    if not cache.add(flush_lock_key, "1", timeout=PENDING_FLUSH_LOCK_TIMEOUT):
        logger.info("Pending push flush already in progress for user %s", user_id)
        return

    try:
        while True:
            with transaction.atomic():
                pending = (
                    PendingPushNotification.objects
                    .select_for_update()
                    .filter(user=user, sent=False)
                    .order_by("created_at", "id")
                    .first()
                )

                if pending is None:
                    break

                try:
                    success_count, failure_count = send_push_to_user(user, pending.title, pending.body)
                    if success_count > 0:
                        pending.sent = True
                        pending.sent_at = dj_timezone.now()
                        pending.save(update_fields=["sent", "sent_at"])
                        logger.info("Delivered pending notification %s to user %s", pending.id, user)
                    else:
                        logger.warning("Pending notification %s not delivered (retry later)", pending.id)
                except Exception:
                    logger.exception("Failed while processing pending notification %s", pending.id)
    finally:
        cache.delete(flush_lock_key)

# -------------------------------------------------------------------
# 📧 Task: Send booking assignment email + push
# -------------------------------------------------------------------
# -------------------------------------------------------------------
# 📧 Task: Send booking assignment email + push
# -------------------------------------------------------------------
@shared_task
def send_surveyor_assignment_email_and_push(surveyor_id, booking_id, client_name, service_name, scheduled_date):
    """
    Sends both an email and push notification when a new booking is assigned.
    Automatically queues push notifications if user has no active FCM tokens.
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()

    try:
        surveyor = User.objects.get(pk=surveyor_id)
    except User.DoesNotExist:
        logger.error(f"Surveyor {surveyor_id} not found for booking {booking_id}")
        return

    # --- Email ---
    subject = f"New Booking Assigned - {service_name}"
    greeting_name = surveyor.get_full_name().strip() or "there"
    message = (
        f"Hello {greeting_name},\n\n"
        f"You have been assigned a new booking for {service_name}.\n"
        f"Client: {client_name}\n"
        f"Scheduled Date: {scheduled_date}\n\n"
        f"Please log in to your account to review details.\n\n"
        f"Thank you,\n{settings.DEFAULT_FROM_EMAIL}"
    )

    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [surveyor.email],
        fail_silently=False,
    )

    # --- Push Notification ---
    send_push_to_user(
        surveyor,
        title="📅 New Booking Assigned",
        body=f"{service_name} for {client_name} scheduled at {scheduled_date}.",
    )

    # --- Schedule Reminder 2 hours before ---
        # --- Schedule Reminder 2 hours before ---
    try:
        # if no scheduled_date provided, skip scheduling
        if not scheduled_date:
            logger.info("No scheduled_date passed for booking %s — skipping reminder scheduling", booking_id)
        else:
            # parse ISO timestamp robustly (will raise on invalid)
            scheduled_dt = datetime.fromisoformat(scheduled_date)

            # if naive (no tzinfo), make it aware — we use UTC as canonical fallback
            if dj_timezone.is_naive(scheduled_dt):
                scheduled_dt = dj_timezone.make_aware(scheduled_dt, dt_timezone.utc)
                logger.debug("scheduled_date was naive; made aware (UTC): %s", scheduled_dt)

            reminder_time = scheduled_dt - timedelta(hours=2)

            # compare using django timezone helpers (aware)
            now = dj_timezone.now()
            if reminder_time > now:
                send_surveyor_reminder_email_and_push.apply_async(
                    args=[surveyor.id, client_name, service_name, scheduled_date],
                    eta=reminder_time
                )
                logger.info("⏰ Reminder for booking %s scheduled at %s (now=%s)", booking_id, reminder_time, now)
            else:
                logger.info("⚠️ Scheduled date already passed (or within 2 hours) for booking %s: scheduled=%s now=%s",
                            booking_id, scheduled_dt, now)
    except Exception as e:
        logger.exception("Failed to schedule reminder for booking %s: %s", booking_id, e)


# -------------------------------------------------------------------
# ⏰ Task: Send reminder (email + push)
# -------------------------------------------------------------------
@shared_task
def send_surveyor_reminder_email_and_push(surveyor_id, client_name, service_name, scheduled_date):
    """
    Sends reminder email and push 2 hours before scheduled booking.
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()

    try:
        surveyor = User.objects.get(pk=surveyor_id)
    except User.DoesNotExist:
        logger.error(f"Surveyor {surveyor_id} not found for reminder.")
        return

    subject = f"Reminder: Upcoming Booking for {service_name}"
    greeting_name = surveyor.get_full_name().strip() or "there"
    message = (
        f"Hello {greeting_name},\n\n"
        f"This is a reminder for your upcoming booking:\n"
        f"Service: {service_name}\n"
        f"Client: {client_name}\n"
        f"Scheduled for: {scheduled_date}\n\n"
        f"Please ensure you are prepared and on time.\n\n"
        f"Thank you,\n{settings.DEFAULT_FROM_EMAIL}"
    )

    # --- Send Email ---
    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [surveyor.email],
        fail_silently=False,
    )

    # --- Send Push Notification ---
    send_push_to_user(
        surveyor,
        title="⏰ Reminder: Upcoming Booking",
        body=f"{service_name} with {client_name} at {scheduled_date}.",
    )

    logger.info(f"✅ Reminder email + push sent to {surveyor} for {service_name}")


# -------------------------------------------------------------------
# 📋 Optional: Booking handled summary (for admins)
# -------------------------------------------------------------------
@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_handled_summary_email(self, booking_id, recipient_emails, include_client=False):
    """
    Sends a summary email when a booking is marked as handled.
    """
    try:
        booking = Booking.objects.select_related(
            "client_service__client",
            "client_service__service"
        ).get(pk=booking_id)
    except Booking.DoesNotExist:
        logger.exception(f"Booking {booking_id} does not exist")
        return

    service_name = booking.client_service.service.name if booking.client_service and booking.client_service.service else "Service"
    client_name = (
        getattr(booking.client_service.client, "get_full_name", lambda: str(booking.client_service.client))()
        if booking.client_service and booking.client_service.client else "Client"
    )

    subject = f"Booking #{booking.id} marked handled — {service_name}"
    context = {
        "booking": booking,
        "service_name": service_name,
        "client_name": client_name,
        "handled_by": booking.handled_by.get_full_name() if booking.handled_by else str(booking.handled_by),
        "handled_at": booking.handled_at,
    }

    text_body = render_to_string("emails/booking_handled_summary.txt", context)
    html_body = render_to_string("emails/booking_handled_summary.html", context)

    try:
        msg = EmailMultiAlternatives(subject, text_body, settings.DEFAULT_FROM_EMAIL, recipient_emails)
        msg.attach_alternative(html_body, "text/html")
        msg.send(fail_silently=False)
        logger.info(f"📩 Handled summary email sent for booking {booking_id}")
    except Exception as exc:
        logger.exception(f"Failed to send handled summary email for booking {booking_id}")
        raise self.retry(exc=exc)
