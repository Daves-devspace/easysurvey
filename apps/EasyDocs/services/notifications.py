"""
apps/EasyDocs/services/notifications.py

Helper functions for creating notifications (DB + realtime + email) related to bookings.

Responsibilities:
- create_booking_notifications: called when surveyors are assigned to a booking.
- create_handled_notifications: called when a booking is marked as handled.
- display_name: safe utility to get a human-friendly name for model instances.

Design notes:
- Uses get_user_model() so it works with custom user models.
- Channels messages are sent under a per-user group named `user_{user.id}`.
  The payload is sent under the "data" key; your consumer should do:
      await self.send(text_data=json.dumps(event["data"]))
- DB Notification creation is minimal (title, message) to avoid assuming extra fields.
- The Channels payload contains a `link` value for frontend navigation, but the
  Notification model is not required to have a `link` field. Add the field + migration
  if you want the DB to store the link too.
"""

from typing import Iterable
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from apps.notifications.models import Notification  # your Notification model

logger = logging.getLogger(__name__)
User = get_user_model()


def display_name(obj) -> str:
    """
    Safely return a human-friendly name for model instances.

    Tries in order:
    - obj.get_full_name() (if exists)
    - obj.first_name + obj.last_name
    - obj.name
    - str(obj)
    Returns "Unknown" when nothing sensible is found.
    """
    if obj is None:
        return "Unknown"

    # Preferred method
    if hasattr(obj, "get_full_name"):
        try:
            name = obj.get_full_name()
            if name:
                return name
        except Exception:
            # if get_full_name exists but fails, continue to other fallbacks
            pass

    # first_name + last_name fallback
    first = getattr(obj, "first_name", None)
    last = getattr(obj, "last_name", None)
    if first or last:
        return f"{first or ''} {last or ''}".strip()

    # generic name attribute
    name_attr = getattr(obj, "name", None)
    if name_attr:
        return str(name_attr)

    # final fallback
    try:
        return str(obj)
    except Exception:
        return "Unknown"


def create_booking_notifications(booking, surveyor_ids: Iterable[int]) -> list:
    """
    Notify surveyors & superadmins when surveyors are assigned to a booking.

    For each surveyor:
      - create a DB Notification (title + message)
      - send a Channels group message to user_{surveyor.id}
      - enqueue an async assignment email task (if available)

    For superadmins:
      - create an aggregate DB Notification summarizing assigned surveyors
      - push a Channels message to each superadmin

    Args:
        booking: Booking instance (expected to have client_service relation)
        surveyor_ids: iterable of surveyor user IDs to notify

    Returns:
        list of assigned surveyor User objects (successful fetches)
    """
    logger.info("create_booking_notifications() triggered for booking %s", booking.id)
    logger.debug("Surveyor IDs received: %s", list(surveyor_ids))
    channel_layer = get_channel_layer()
    assigned_surveyors = []

    # Safe lookups (booking may not have all relations loaded)
    cs = getattr(booking, "client_service", None)
    service = getattr(cs, "service", None)
    client = getattr(cs, "client", None)
    service_name = display_name(service)
    client_name = display_name(client)
    booking_id = getattr(booking, "id", "")

    for uid in surveyor_ids:
        try:
            surveyor = User.objects.get(pk=uid)
        except User.DoesNotExist:
            logger.warning("create_booking_notifications: surveyor id %s does not exist, skipping", uid)
            continue

        assigned_surveyors.append(surveyor)

        # 1) DB Notification: minimal fields so model changes won't break this helper
        try:
            Notification.objects.create(
                user=surveyor,
                title="New Booking Assigned",
                message=f"You've been assigned to {service_name} for {client_name}.",
            )
        except Exception as exc:
            logger.exception("Failed to create Notification object for surveyor %s: %s", surveyor.id, exc)

        # 2) Channels realtime push (frontend expects event["data"])
        try:
            async_to_sync(channel_layer.group_send)(
                f"user_{surveyor.id}",
                {
                    "type": "send_notification",
                    "data": {
                        "id": None,
                        "title": "New Booking Assigned",
                        "message": f"You've been assigned to {service_name} for {client_name}.",
                        # `link` is provided for the frontend; add a link field to Notification model if you want to persist it
                        "link": f"/bookings/{booking_id}/details/" if booking_id else None,
                        "created_at": getattr(booking, "scheduled_date", None).isoformat() if getattr(booking, "scheduled_date", None) else None,
                    },
                },
            )
        except Exception as exc:
            logger.exception("Failed to send Channels message for surveyor %s: %s", surveyor.id, exc)

        # 3) Enqueue email task (if task exists). Import inside to avoid top-level circular imports.
        try:
            from apps.notifications.tasks import send_surveyor_assignment_email_and_push
            # send_surveyor_assignment_email signature (email, booking_id, client_name, service_name, scheduled_date)
            send_surveyor_assignment_email_and_push.delay(
                surveyor.id,
                booking_id,
                client_name,
                service_name,
                str(getattr(booking, "scheduled_date", "")),
            )

        except Exception as exc:
            # If the task or sending fails, log but continue — we don't want to block the view.
            logger.exception("Failed to enqueue assignment email for surveyor %s: %s", surveyor.id, exc)

    # Aggregate notifications to superadmins
    try:
        superadmins = User.objects.filter(is_superuser=True)
        if assigned_surveyors and superadmins.exists():
            surveyor_names = ", ".join([display_name(s) for s in assigned_surveyors])
            summary_message = f"Surveyors {surveyor_names} have been assigned to {service_name} for {client_name}."

            for admin in superadmins:
                try:
                    Notification.objects.create(
                        user=admin,
                        title="Surveyor Assignment Update",
                        message=summary_message,
                    )
                except Exception:
                    logger.exception("Failed to create aggregate Notification for admin %s", admin.id)

                try:
                    async_to_sync(channel_layer.group_send)(
                        f"user_{admin.id}",
                        {
                            "type": "send_notification",
                            "data": {
                                "id": None,
                                "title": "Surveyor Assignment Update",
                                "message": summary_message,
                                "link": f"/bookings/{booking_id}/details/" if booking_id else None,
                                "created_at": getattr(booking, "scheduled_date", None).isoformat() if getattr(booking, "scheduled_date", None) else None,
                            },
                        },
                    )
                except Exception as exc:
                    logger.exception("Failed to push Channels message to admin %s: %s", admin.id, exc)
    except Exception as exc:
        logger.exception("Error when notifying superadmins: %s", exc)

    return assigned_surveyors


def create_handled_notifications(
    booking,
    final_surveyor_ids: Iterable[int],
    handled_by_user,
    notify_client: bool = True
) -> list:
    """
    Notify final surveyors, superadmins, and optionally client when a booking is marked handled.

    For final surveyors:
      - create DB Notification
      - push Channels message (personal)

    For superadmins:
      - create a summary DB Notification
      - push Channels message (summary)

    Optionally:
      - notify client when client has an associated user.

    Returns:
        list of assigned surveyor User objects
    """
    channel_layer = get_channel_layer()
    assigned_surveyors = []

    cs = getattr(booking, "client_service", None)
    service = getattr(cs, "service", None)
    client = getattr(cs, "client", None)

    service_name = display_name(service)
    client_name = display_name(client)
    booking_id = getattr(booking, "id", "?")
    handled_at_iso = getattr(booking, "handled_at", None).isoformat() if getattr(booking, "handled_at", None) else None

    # Notify each final surveyor
    for uid in final_surveyor_ids:
        try:
            s = User.objects.get(pk=uid)
        except User.DoesNotExist:
            logger.warning("create_handled_notifications: final surveyor id %s does not exist; skipping.", uid)
            continue

        assigned_surveyors.append(s)

        try:
            Notification.objects.create(
                user=s,
                title="Booking Handled",
                message=f"You were recorded as a final surveyor for {service_name} (Booking #{booking_id}).",
            )
        except Exception:
            logger.exception("Failed to create Booking Handled notification for surveyor %s", s.id)

        try:
            async_to_sync(channel_layer.group_send)(
                f"user_{s.id}",
                {
                    "type": "send_notification",
                    "data": {
                        "id": None,
                        "title": "Booking Handled",
                        "message": f"You were recorded as a final surveyor for {service_name}.",
                        "link": f"/bookings/{booking_id}/details/" if booking_id else None,
                        "created_at": handled_at_iso,
                    },
                },
            )
        except Exception as exc:
            logger.exception("Failed to push Booking Handled Channels message for surveyor %s: %s", s.id, exc)

    # Superadmin summary
    try:
        superadmins = User.objects.filter(is_superuser=True)
        if assigned_surveyors and superadmins.exists():
            surveyor_names = ", ".join([display_name(s) for s in assigned_surveyors])
            handled_by_name = display_name(handled_by_user)
            summary_message = (
                f"Booking #{booking_id} for {client_name} was marked handled by {handled_by_name}. "
                f"Final surveyors: {surveyor_names}."
            )

            for admin in superadmins:
                try:
                    Notification.objects.create(user=admin, title="Booking Completed", message=summary_message)
                except Exception:
                    logger.exception("Failed to create Booking Completed notification for admin %s", admin.id)

                try:
                    async_to_sync(channel_layer.group_send)(
                        f"user_{admin.id}",
                        {
                            "type": "send_notification",
                            "data": {
                                "id": None,
                                "title": "Booking Completed",
                                "message": summary_message,
                                "link": f"/bookings/{booking_id}/details/" if booking_id else None,
                                "created_at": handled_at_iso,
                            },
                        },
                    )
                except Exception as exc:
                    logger.exception("Failed to push Booking Completed Channels message to admin %s: %s", admin.id, exc)
    except Exception as exc:
        logger.exception("Error when creating handled notifications for superadmins: %s", exc)

    # Optionally notify the client (only if the client model links to a User)
    # if notify_client:
    #     try:
    #         client_user = getattr(client, "user", None)
    #         if client_user and getattr(client_user, "id", None):
    #             client_message = f"Your booking (#{booking_id}) for {service_name} has been completed."
    #             try:
    #                 Notification.objects.create(user=client_user, title="Booking Completed", message=client_message)
    #             except Exception:
    #                 logger.exception("Failed to create Booking Completed notification for client user %s", client_user.id)

    #             try:
    #                 async_to_sync(channel_layer.group_send)(
    #                     f"user_{client_user.id}",
    #                     {
    #                         "type": "send_notification",
    #                         "data": {
    #                             "id": None,
    #                             "title": "Booking Completed",
    #                             "message": client_message,
    #                             "link": f"/client/bookings/{booking_id}/" if booking_id else None,
    #                             "created_at": handled_at_iso,
    #                         },
    #                     },
    #                 )
    #             except Exception as exc:
    #                 logger.exception("Failed to push Booking Completed Channels message to client user %s: %s", client_user.id, exc)
    #     except Exception as exc:
    #         logger.exception("Failed to notify client user: %s", exc)

    return assigned_surveyors
