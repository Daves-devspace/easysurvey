from django.test import TestCase
from apps.EasyDocs.bot import paraphrase_text, get_fallback_response, search_kb

class BotCoreTests(TestCase):

    def test_paraphrase_text_returns_string(self):
        text = "Hello world"
        result = paraphrase_text(text)
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_fallback_response_is_string(self):
        response = get_fallback_response()
        self.assertIsInstance(response, str)
        self.assertTrue(len(response) > 0)

    def test_search_kb_empty_returns_list(self):
        results = search_kb("Nonexistent query")
        self.assertIsInstance(results, list)
