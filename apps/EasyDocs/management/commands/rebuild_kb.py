# from django.core.management.base import BaseCommand

# class Command(BaseCommand):
#     help = "Rebuild FAISS index from knowledgeBase.json (lazy imports)"

#     def handle(self, *args, **options):
#         # Import inside the function to avoid heavy top-level imports at module load time.
#         from apps.EasyDocs.bot import _load_kb  # import the function you wrote
#         try:
#             entries, vectors = _load_kb()
#             # Optionally set module-level globals if you want:
#             import importlib
#             module = importlib.import_module("apps.EasyDocs.bot")
#             module.KB_ENTRIES = entries
#             module.KB_EMBEDDINGS = vectors
#             self.stdout.write(self.style.SUCCESS(f"Rebuilt KB. size={len(entries)}"))
#         except Exception as e:
#             self.stderr.write(self.style.ERROR(f"Rebuild failed: {e}"))
#             raise
