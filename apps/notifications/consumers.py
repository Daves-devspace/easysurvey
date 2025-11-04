# notifications/consumers.py
import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async



class NotificationConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope['user']
        
        from django.contrib.auth.models import AnonymousUser

        if user is None or isinstance(user, AnonymousUser):
            await self.close()
            return

        self.user = user

        # Normal user group
        self.group_name = f"user_{user.id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)

        # Superusers also join a global monitoring group
        if user.is_superuser:
            await self.channel_layer.group_add("superusers", self.channel_name)

        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)
        if self.user.is_superuser:
            await self.channel_layer.group_discard("superusers", self.channel_name)

    async def receive(self, text_data):
        # Optional: handle frontend commands like "mark_read"
        pass

    async def send_notification(self, event):
        await self.send(text_data=json.dumps(event["data"]))



class TestConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.accept()
        await self.send(text_data=json.dumps({"message": "✅ WebSocket connected successfully!"}))

    async def disconnect(self, close_code):
        print("WebSocket disconnected")

    async def receive(self, text_data):
        await self.send(text_data=json.dumps({"echo": text_data}))