from django.test import TestCase
from apps.EasyDocs.bot import _load_kb_inprocess, _load_persisted_index

class BotKBTests(TestCase):

    def test_load_kb_inprocess_returns_entries(self):
        entries, embeddings = _load_kb_inprocess(persist_on_build=False)
        self.assertIsInstance(entries, list)
        if embeddings is not None:
            self.assertEqual(embeddings.shape[0], len(entries))

    def test_load_persisted_index_structure(self):
        entries, embeddings = _load_persisted_index()
        self.assertIsInstance(entries, list)
