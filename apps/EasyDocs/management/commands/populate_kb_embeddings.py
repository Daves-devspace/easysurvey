import json
import requests
from pathlib import Path
from django.core.management.base import BaseCommand
from django.conf import settings
from apps.EasyDocs.models import KnowledgeEntry

HF_API_URL = "https://api-inference.huggingface.co/models/sentence-transformers/all-MiniLM-L6-v2"
HF_API_KEY = settings.HF_API_KEY  # from settings.py

class Command(BaseCommand):
    help = "Populate embeddings for KnowledgeEntry, optionally from a JSON file"

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            help="Path to JSON knowledge base file to load before embedding"
        )

    def handle(self, *args, **options):
        headers = {"Authorization": f"Bearer {HF_API_KEY}"}

        # 1. Load JSON file if provided
        if options["file"]:
            kb_path = Path(options["file"])
            if not kb_path.exists():
                self.stdout.write(self.style.ERROR(f"❌ File not found: {kb_path}"))
                return

            with open(kb_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.stdout.write(f"📂 Loading from JSON: {kb_path}")
            for item in data:
                q = item.get("question")
                a = item.get("answer")
                if not (q and a):
                    continue
                obj, created = KnowledgeEntry.objects.get_or_create(
                    question=q.strip(),
                    defaults={"answer": a.strip()}
                )
                if created:
                    self.stdout.write(self.style.SUCCESS(f"Added: {q[:50]}..."))

        # 2. Embed all entries missing embeddings
        entries = KnowledgeEntry.objects.filter(embedding__isnull=True)
        self.stdout.write(f"👋 Hi root, preparing to populate embeddings... Found {entries.count()} entries to embed.")

        for entry in entries:
            try:
                response = requests.post(
                    HF_API_URL,
                    headers=headers,
                    json={"inputs": entry.question},  # Hugging Face expects under "inputs"
                    timeout=30
                )

                if response.status_code != 200:
                    self.stdout.write(self.style.ERROR(
                        f"Failed: {entry.question[:50]}... ({response.status_code})"
                    ))
                    continue

                embedding = response.json()
                # Some HF models return [[...]] instead of [...]
                if isinstance(embedding, list) and isinstance(embedding[0], list):
                    embedding = embedding[0]

                entry.embedding = embedding
                entry.save(update_fields=["embedding"])
                self.stdout.write(self.style.SUCCESS(f"Embedded: {entry.question[:50]}..."))

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error: {str(e)}"))
