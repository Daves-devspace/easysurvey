# apps/EasyDocs/scripts/embed_knowledge_base.py

import json
import time
from sentence_transformers import SentenceTransformer

# === CONFIG ===
INPUT_PATH = "static/assets/json/knowledgeBase.json"
OUTPUT_PATH = "static/assets/json/knowledgeBase_with_embeddings.json"
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"  # free & lightweight

def main():
    # load local HF model
    print(f"🔄 Loading embedding model: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)

    # load KB
    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        kb = json.load(f)

    enriched = []
    for i, item in enumerate(kb):
        q = item.get("question", "").strip()
        a = item.get("answer", "").strip()
        text_for_embedding = f"Q: {q}\nA: {a}"

        try:
            embedding = model.encode(text_for_embedding).tolist()
            item["embedding"] = embedding
            print(f"[{i+1}/{len(kb)}] ✅ Embedded: {q[:30]}...")
        except Exception as e:
            print(f"[{i+1}/{len(kb)}] ❌ Failed: {q[:30]}... Error: {e}")
            item["embedding"] = None

        enriched.append(item)
        time.sleep(0.2)  # gentle throttling (not really needed locally)

    # save enriched KB
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=2)

    print(f"\n✅ Done. Enriched file saved to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
