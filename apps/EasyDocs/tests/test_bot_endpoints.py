from django.test import TestCase, Client
import json

class BotEndpointTests(TestCase):

    def setUp(self):
        self.client = Client()

    def test_bot_health_endpoint(self):
        response = self.client.get("/api/bot_health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("status", data)
        self.assertEqual(data["status"], "healthy")

    def test_get_similarity_missing_query(self):
        response = self.client.post("/api/get_similarity", data=json.dumps({}), content_type="application/json")
        self.assertEqual(response.status_code, 400)
