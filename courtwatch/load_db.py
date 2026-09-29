#!/usr/bin/env python3
"""
Load validated EDISON records into SQLite (structured, queryable) and Qdrant (semantic search).

SQLite  /data/edison.db
  records      one row per record: id, type, dates, amounts, entity tags, flags, sha256, full JSON
  provenance   sha256 -> file name/path/first seen   (chain of custody index)
  flags        record_id, flag                       (fast ATTORNEY_FLAG / BANKRUPTCY_FLAG queries)
Qdrant  collection "edison_records"
  vector = embedding(verbatim_extract), payload = the metadata used for pre-filtering
  (record_type, entity_tags, transaction_date, amount_cents, flags, sha256).  Scalar-quantized.

Idempotent: same record_id or same (sha256, page_ref, verbatim_extract) is not inserted twice.
"""
import argparse, hashlib, json, sqlite3, uuid
import requests
from qdrant_client import QdrantClient
from qdrant_client.models import (Distance, VectorParams, PointStruct, ScalarQuantization,
                                  ScalarQuantizationConfig, ScalarType)

DDL = """
CREATE TABLE IF NOT EXISTS provenance (
  sha256 TEXT PRIMARY KEY, source_file_name TEXT, source_path TEXT, first_seen TEXT);
CREATE TABLE IF NOT EXISTS records (
  record_id TEXT PRIMARY KEY, dedupe_key TEXT UNIQUE, record_type TEXT, sha256 TEXT,
  page_ref TEXT, transaction_date TEXT, document_date TEXT, amount_raw TEXT, amount_cents INTEGER,
  merchant_raw TEXT, entity_tags TEXT, ocr_confidence TEXT, human_verified INTEGER DEFAULT 0,
  ingested_at TEXT, verbatim_extract TEXT, json TEXT NOT NULL,
  FOREIGN KEY(sha256) REFERENCES provenance(sha256));
CREATE TABLE IF NOT EXISTS flags (record_id TEXT, flag TEXT, PRIMARY KEY(record_id, flag));
CREATE INDEX IF NOT EXISTS ix_records_date ON records(transaction_date);
CREATE INDEX IF NOT EXISTS ix_records_type ON records(record_type);
CREATE INDEX IF NOT EXISTS ix_flags_flag ON flags(flag);
"""

def embed(ollama, model, text):
    r = requests.post(f"{ollama}/api/embeddings", json={"model": model, "prompt": text[:8000]}, timeout=120)
    r.raise_for_status(); return r.json()["embedding"]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", required=True)
    ap.add_argument("--db", default="/data/edison.db")
    ap.add_argument("--qdrant", default="http://qdrant:6333")
    ap.add_argument("--ollama", default="http://ollama:11434")
    ap.add_argument("--embed-model", default="nomic-embed-text")
    ap.add_argument("--collection", default="edison_records")
    a = ap.parse_args()

    con = sqlite3.connect(a.db); con.executescript(DDL)
    qc = QdrantClient(url=a.qdrant)
    dim = None
    n_new = n_dup = 0
    points = []
    for line in open(a.records):
        rec = json.loads(line)
        prov, fin, dates = rec.get("provenance", {}), rec.get("financial", {}) or {}, rec.get("dates", {}) or {}
        sha = prov.get("source_file_sha256", "MISSING_DATA")
        # Dedupe key: same source page + same verbatim + same extracted facts. Several records from one page
        # legitimately share the page's verbatim text (e.g. 3 transactions), so the key must include the
        # record's own fields or all but the first would be dropped as duplicates.
        med = rec.get("medical", {}) or {}
        dk = hashlib.sha256("|".join(str(x) for x in (
            sha, prov.get("page_or_line_ref"), prov.get("verbatim_extract"), rec.get("record_type"),
            dates.get("transaction_date"), dates.get("document_date"), dates.get("event_date"),
            fin.get("amount_raw"), fin.get("merchant_raw"), fin.get("direction"),
            med.get("provider_name"), med.get("treatment_description"),
        )).encode()).hexdigest()
        flags = prov.get("extraction_flags", []) or []
        con.execute("INSERT OR IGNORE INTO provenance VALUES (?,?,?,?)",
                    (sha, prov.get("source_file_name"), prov.get("source_path"), prov.get("ingested_at")))
        try:
            con.execute("""INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                rec["record_id"], dk, rec.get("record_type"), sha, prov.get("page_or_line_ref"),
                dates.get("transaction_date"), dates.get("document_date"),
                fin.get("amount_raw"), fin.get("amount_cents"), fin.get("merchant_raw"),
                json.dumps(rec.get("entity_tags", [])), prov.get("ocr_confidence"),
                1 if rec.get("extraction", {}).get("human_verified") else 0,
                prov.get("ingested_at"), prov.get("verbatim_extract"), json.dumps(rec, ensure_ascii=False)))
        except sqlite3.IntegrityError:
            n_dup += 1; continue
        con.executemany("INSERT OR IGNORE INTO flags VALUES (?,?)", [(rec["record_id"], f) for f in flags])
        n_new += 1
        text = prov.get("verbatim_extract") or json.dumps(rec)
        vec = embed(a.ollama, a.embed_model, text); dim = dim or len(vec)
        points.append(PointStruct(
            id=str(uuid.UUID(rec["record_id"])), vector=vec,
            payload={"record_id": rec["record_id"], "record_type": rec.get("record_type"), "sha256": sha,
                     "page_ref": prov.get("page_or_line_ref"), "entity_tags": rec.get("entity_tags", []),
                     "transaction_date": dates.get("transaction_date"), "document_date": dates.get("document_date"),
                     "amount_cents": fin.get("amount_cents"), "merchant_raw": fin.get("merchant_raw"),
                     "flags": flags, "human_verified": bool(rec.get("extraction", {}).get("human_verified")),
                     "text": text}))
    con.commit()
    if points:
        if not qc.collection_exists(a.collection):
            qc.create_collection(a.collection, vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
                                 quantization_config=ScalarQuantization(scalar=ScalarQuantizationConfig(
                                     type=ScalarType.INT8, quantile=0.99, always_ram=True)))
            for f in ("record_type", "entity_tags", "flags", "transaction_date", "sha256"):
                qc.create_payload_index(a.collection, f, "keyword")
        qc.upsert(a.collection, points=points, wait=True)
    print(f"[load_db] sqlite new={n_new} dup={n_dup}  qdrant upserted={len(points)}")

if __name__ == "__main__":
    main()
