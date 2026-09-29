"""
Export the "me" person's court-watch entries into EDISON (edison.db + Qdrant) through the same load_db.py the
miner uses. Only rows for people.is_me=1 are ever exported; everyone else stays in court_watch.db.

Zero-hallucination rules:
  - verbatim_extract is the row text exactly as parsed from the page (label: value | ...), never edited
  - dates come only from the row's own date field; otherwise MISSING_DATA
  - provenance = the snapshot file (exact bytes fetched) + its SHA-256 + the URL
  - record ids are uuid5 of (site, case, entry_key) so re-exports never duplicate
"""
import json, os, subprocess, sys, uuid, datetime, pathlib
from jsonschema import Draft202012Validator
from .sites.base import mdy_to_iso, DATA

NS = uuid.UUID("6f1c2a4e-3b7d-4c8e-9a1f-2d3e4f5a6b7c")
SCHEMA = os.environ.get("CW_SCHEMA", "/app/schema.json")
LOAD_DB = os.environ.get("CW_LOAD_DB", "/app/load_db.py")

KIND_TO_TYPE = {"docket": "court_filing", "calendar": "court_calendar_event", "event": "court_calendar_event",
                "party": "court_filing", "charge": "criminal_case_document", "disposition": "criminal_case_document",
                "sentence": "criminal_case_document", "payment": "financial_transaction", "balance": "financial_account_doc",
                "warrant": "criminal_case_document", "note": "other", "header": "court_filing", "import_row": "other"}

def _record(person, case, entry, site_label):
    fields = json.loads(entry["fields_json"] or "{}")
    kind = entry["kind"]
    rtype = KIND_TO_TYPE.get(kind, "other")
    if site_label.startswith("Maricopa County Superior Court - Family") and rtype == "criminal_case_document":
        rtype = "court_filing"
    iso = mdy_to_iso(entry["date"] or "")
    flags = ["court_watch", f"site:{case['site']}", f"kind:{kind}", "web_page_not_ocr"]
    if not iso:
        flags.append("date_missing_on_row")
    rec = {
        "record_id": str(uuid.uuid5(NS, f"courtwatch|{case['site']}|{case['case_number']}|{entry['entry_key']}")),
        "provenance": {
            "source_file_name": pathlib.Path(entry["snapshot_path"] or "").name or "MISSING_DATA",
            "source_file_sha256": entry["snapshot_sha"] or "MISSING_DATA",
            "source_path": entry["source_url"] or case["url"] or "MISSING_DATA",
            "ingested_at": entry["first_seen"],
            "page_or_line_ref": f"{case['site']}:{case['case_number']}:{kind}:{entry['entry_key']}",
            "verbatim_extract": entry["text"],
            "ocr_confidence": "high",
            "extraction_flags": sorted(set(flags)),
        },
        "record_type": rtype,
        "case_association": {"case_number": case["case_number"], "court": case["court"] or site_label or "MISSING_DATA"},
        "entity_tags": [person["entity_id"] or "MISSING_DATA"],
        "dates": {"event_date": iso or "MISSING_DATA", "document_date": iso or "MISSING_DATA",
                  "date_confidence": "exact" if iso else "MISSING_DATA"},
        "extraction": {"extracted_by": "EDISON-CourtWatch-v1", "extraction_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       "schema_version": "1.0.0", "human_verified": False, "human_verified_by": None, "human_verified_at": None},
    }
    if rtype == "financial_transaction":
        amt = next((v for k, v in fields.items() if "amount" in k.lower() or "paid" in k.lower()), "")
        rec["financial"] = {"amount_raw": amt or "MISSING_DATA", "amount_cents": None, "direction": "MISSING_DATA",
                            "category": "court_fines", "payee_or_payor": site_label or "MISSING_DATA"}
    return rec

def export_me(store, log=print):
    """Write un-exported entries of the is_me person to JSONL and load them. Returns (written, rejected, loaded_msg)."""
    me = store.one("SELECT * FROM people WHERE is_me=1 AND active=1")
    if not me:
        return 0, 0, "no is_me person"
    validator = Draft202012Validator(json.load(open(SCHEMA)))
    out = DATA / "edison_export.jsonl"
    written = rejected = 0
    ids = []
    with open(out, "w") as f:
        for case in store.cases_for(me["id"]):
            for e in store.q("SELECT * FROM entries WHERE case_id=? AND exported=0", case["id"]):
                rec = _record(me, case, e, case["court"])
                errs = [f"{'/'.join(map(str, x.path))}: {x.message}" for x in validator.iter_errors(rec)]
                if errs:
                    rejected += 1; log(f"[export] reject {case['case_number']} {e['kind']}: {errs[:2]}"); continue
                f.write(json.dumps(rec, ensure_ascii=False) + "\n"); written += 1; ids.append(e["id"])
    if not written:
        return 0, rejected, "nothing new"
    cmd = [sys.executable, LOAD_DB, "--records", str(out), "--db", os.environ.get("EDISON_DB", "/data/edison.db"),
           "--qdrant", os.environ.get("QDRANT_URL", "http://qdrant:6333"), "--ollama", os.environ.get("OLLAMA_URL", "http://ollama:11434"),
           "--embed-model", os.environ.get("EMBED_MODEL", "nomic-embed-text")]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    msg = (r.stdout + r.stderr).strip()[-500:]
    if r.returncode == 0:
        for i in ids:
            store.x("UPDATE entries SET exported=1 WHERE id=?", i)
    log(f"[export] {written} records -> load_db rc={r.returncode}: {msg}")
    return written, rejected, msg
