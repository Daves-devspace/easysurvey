# apps/notifications/management/commands/test_fcm.py
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.notifications.models import FCMToken
from apps.notifications.utils import send_push_notification
import json

User = get_user_model()

class Command(BaseCommand):
    help = "Test FCM sending for a given user (email or id)."

    def add_arguments(self, parser):
        parser.add_argument("user", type=str, help="User email or id")
        parser.add_argument("--message", type=str, default="FCM test", help="Message body")

    def handle(self, *args, **options):
        u = options["user"]
        msg = options["message"]

        try:
            user = User.objects.get(pk=int(u)) if u.isdigit() else User.objects.get(email=u)
        except Exception as e:
            self.stderr.write(f"User lookup failed: {e}")
            return

        tokens = list(FCMToken.objects.filter(user=user, is_active=True).values_list("token", flat=True))
        self.stdout.write(f"Found {len(tokens)} active token(s) for user {user}:\n")
        for t in tokens:
            self.stdout.write(json.dumps({"token": t[:40]+"..." if t else "", "len": len(t)}))

        if not tokens:
            self.stdout.write("No active tokens to test. Exiting.")
            return

        # Send to each token and print results
        for t in tokens:
            self.stdout.write(f"\n== Testing token (prefix) {t[:40]} ==")
            try:
                resp = send_push_notification(t, "Test: " + msg, msg)
                self.stdout.write(f"send_push_notification returned: {resp}")
            except Exception as exc:
                self.stderr.write(f"Exception when sending to token: {exc}")
