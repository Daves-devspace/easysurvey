# scripts/build_faiss.py
import os
import json
import faiss
import numpy as np
from pathlib import Path
from sentence_transformers import SentenceTransformer
import torch

BASE_DIR = Path(__file__).resolve().parents[1]
KB_FILE = BASE_DIR / "static" / "assets" / "json" / "knowledgeBase.json"
OUT_DIR = Path(os.environ.get("KB_INDEX_OUT", "/tmp/kb_index"))
OUT_DIR.mkdir(parents=True, exist_ok=True)

EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
print("Loading embedder:", EMBEDDING_MODEL)
embedder = SentenceTransformer(EMBEDDING_MODEL)

data = json.loads(KB_FILE.read_text(encoding="utf-8"))
questions = []
answers = []
vecs = []
for e in data:
    q = e.get("question")
    a = e.get("answer")
    if not q or not a: continue
    questions.append(q.strip())
    answers.append(a.strip())
    vecs.append(embedder.encode(q, convert_to_numpy=True))

if not vecs:
    raise SystemExit("No vectors built")

matrix = np.stack(vecs).astype('float32')
dim = matrix.shape[1]
index = faiss.IndexFlatL2(dim)
index.add(matrix)

faiss.write_index(index, str(OUT_DIR / "kb.faiss"))
np.save(str(OUT_DIR / "embeddings.npy"), matrix)
with open(OUT_DIR / "kb_meta.json", "w", encoding="utf-8") as fh:
    json.dump({"questions": questions, "answers": answers}, fh, ensure_ascii=False)

print("Wrote index to", OUT_DIR)
