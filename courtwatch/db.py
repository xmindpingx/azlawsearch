"""
court_watch.db -- the Monitoring database. Everyone on the names list (including "me") lives here.
Only the person flagged is_me=1 is ALSO exported into the EDISON "my life" store (edison.db + Qdrant);
nobody else's data ever goes there.
"""
import json, os, sqlite3, pathlib, datetime, threading

DB_PATH = pathlib.Path(os.environ.get("CW_DB", "/data/court_watch/court_watch.db"))
_lock = threading.RLock()

DDL = """
CREATE TABLE IF NOT EXISTS people (
  id INTEGER PRIMARY KEY, last TEXT NOT NULL, first TEXT NOT NULL, middle TEXT DEFAULT '', dob TEXT DEFAULT '',
  note TEXT DEFAULT '', is_me INTEGER DEFAULT 0, active INTEGER DEFAULT 1, created_at TEXT, entity_id TEXT DEFAULT '',
  UNIQUE(last, first, dob));
CREATE TABLE IF NOT EXISTS cases (
  id INTEGER PRIMARY KEY, person_id INTEGER, site TEXT, case_number TEXT, court TEXT DEFAULT '', url TEXT DEFAULT '',
  title TEXT DEFAULT '', header_json TEXT DEFAULT '{}', party_name TEXT DEFAULT '', dob_shown TEXT DEFAULT '',
  first_seen TEXT, last_seen TEXT, last_change TEXT, last_fetch_status TEXT DEFAULT '', last_fetch_error TEXT DEFAULT '',
  monitor INTEGER DEFAULT 1, UNIQUE(person_id, site, case_number));
CREATE TABLE IF NOT EXISTS entries (
  id INTEGER PRIMARY KEY, case_id INTEGER, entry_key TEXT, kind TEXT, date TEXT DEFAULT '', text TEXT, fields_json TEXT,
  first_seen TEXT, snapshot_sha TEXT DEFAULT '', snapshot_path TEXT DEFAULT '', source_url TEXT DEFAULT '',
  exported INTEGER DEFAULT 0, UNIQUE(case_id, entry_key));
CREATE INDEX IF NOT EXISTS ix_entries_case ON entries(case_id);
CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY, site TEXT, key TEXT, url TEXT, fetched_at TEXT, sha256 TEXT, path TEXT, bytes INTEGER);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT DEFAULT '', trigger_ TEXT, summary_json TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS run_results (
  id INTEGER PRIMARY KEY, run_id INTEGER, person_id INTEGER, site TEXT, status TEXT, found INTEGER DEFAULT 0,
  new_items INTEGER DEFAULT 0, message TEXT DEFAULT '', at TEXT, detail_json TEXT DEFAULT '{}');
CREATE INDEX IF NOT EXISTS ix_rr_person ON run_results(person_id, at);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY, created_at TEXT, person_id INTEGER, site TEXT, case_number TEXT DEFAULT '', kind TEXT,
  text TEXT, seen INTEGER DEFAULT 0, url TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS warrant_checks (
  id INTEGER PRIMARY KEY, person_id INTEGER, checked_at TEXT, status TEXT, text TEXT, snapshot_sha TEXT, snapshot_path TEXT, url TEXT);
CREATE TABLE IF NOT EXISTS mcso_roster (
  booking_no TEXT PRIMARY KEY, name TEXT, race TEXT, sex TEXT, dob TEXT, status TEXT, age TEXT, booking_date TEXT,
  arrival_date TEXT, expected_release TEXT, detail_url TEXT, first_seen TEXT, last_seen TEXT, gone_at TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_mcso_name ON mcso_roster(name);
CREATE TABLE IF NOT EXISTS mcso_matches (
  id INTEGER PRIMARY KEY, person_id INTEGER, booking_no TEXT, first_seen TEXT, UNIQUE(person_id, booking_no));
CREATE TABLE IF NOT EXISTS mcso_fetches (id INTEGER PRIMARY KEY, at TEXT, title TEXT, rows INTEGER, new_rows INTEGER, snapshot_path TEXT, sha256 TEXT);
CREATE TABLE IF NOT EXISTS chandler_bookings (
  id TEXT PRIMARY KEY, arrest_number TEXT, arrest_date TEXT, arrest_time TEXT, arrest_charge TEXT, arrest_address TEXT,
  arrest_city TEXT, related_offense_report_number TEXT, arrestee_unique_number TEXT, data_json TEXT, first_seen TEXT);
CREATE INDEX IF NOT EXISTS ix_ch_date ON chandler_bookings(arrest_date);
CREATE TABLE IF NOT EXISTS chandler_fetches (id INTEGER PRIMARY KEY, at TEXT, rows INTEGER, new_rows INTEGER, snapshot_path TEXT, sha256 TEXT);
CREATE TABLE IF NOT EXISTS powerbi_captures (id INTEGER PRIMARY KEY, at TEXT, url TEXT, dir TEXT, pages INTEGER, responses INTEGER, text TEXT, meta_json TEXT);
CREATE TABLE IF NOT EXISTS imports (id INTEGER PRIMARY KEY, at TEXT, site TEXT, person_id INTEGER, filename TEXT, sha256 TEXT, path TEXT, note TEXT, rows INTEGER);
CREATE TABLE IF NOT EXISTS custody_status (
  id INTEGER PRIMARY KEY, person_id INTEGER, source TEXT, in_custody INTEGER, bond_raw TEXT DEFAULT '', booking_no TEXT DEFAULT '',
  details TEXT DEFAULT '', seen_at TEXT, snapshot_path TEXT DEFAULT '', url TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_custody_person ON custody_status(person_id, id);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(DDL)
    return con

class Store:
    def __init__(self):
        self.con = connect()
    def q(self, sql, *a):
        with _lock:
            return self.con.execute(sql, a).fetchall()
    def one(self, sql, *a):
        r = self.q(sql, *a); return r[0] if r else None
    def x(self, sql, *a):
        with _lock:
            cur = self.con.execute(sql, a); self.con.commit(); return cur.lastrowid
    # ---- people
    def people(self, active_only=True):
        return self.q("SELECT * FROM people" + (" WHERE active=1" if active_only else "") + " ORDER BY is_me DESC, last, first")
    def person(self, pid):
        return self.one("SELECT * FROM people WHERE id=?", pid)
    def add_person(self, last, first, dob="", middle="", note="", is_me=0):
        last, first, dob = last.strip(), first.strip(), dob.strip()
        ent = f"{first}_{last}".lower().replace(" ", "_")
        ex = self.one("SELECT id FROM people WHERE lower(last)=lower(?) AND lower(first)=lower(?) AND dob=?", last, first, dob)
        if ex:
            self.x("UPDATE people SET active=1, middle=COALESCE(NULLIF(?, ''), middle), note=COALESCE(NULLIF(?, ''), note) WHERE id=?", middle, note, ex["id"])
            return ex["id"], False
        pid = self.x("INSERT INTO people(last, first, middle, dob, note, is_me, active, created_at, entity_id) VALUES (?,?,?,?,?,?,1,?,?)",
                     last, first, middle, dob, note, is_me, now(), ent)
        return pid, True
    def update_person(self, pid, last, first, middle, dob, note, is_me):
        last, first, dob = last.strip(), first.strip(), dob.strip()
        ent = f"{first}_{last}".lower().replace(" ", "_")
        if is_me:
            self.x("UPDATE people SET is_me=0 WHERE id<>?", pid)      # exactly one person is "me"
        self.x("UPDATE people SET last=?, first=?, middle=?, dob=?, note=?, is_me=?, entity_id=? WHERE id=?",
               last, first, middle.strip(), dob, note.strip(), 1 if is_me else 0, ent, pid)
    # ---- cases / entries
    def upsert_case(self, person_id, site, case_number, court="", url="", party_name="", dob_shown="", header=None, title=""):
        ex = self.one("SELECT id FROM cases WHERE person_id=? AND site=? AND case_number=?", person_id, site, case_number)
        t = now()
        if ex:
            self.x("UPDATE cases SET last_seen=?, court=COALESCE(NULLIF(?, ''), court), url=COALESCE(NULLIF(?, ''), url), "
                   "party_name=COALESCE(NULLIF(?, ''), party_name), dob_shown=COALESCE(NULLIF(?, ''), dob_shown), "
                   "header_json=CASE WHEN ? THEN ? ELSE header_json END, title=COALESCE(NULLIF(?, ''), title) WHERE id=?",
                   t, court, url, party_name, dob_shown, 1 if header else 0, json.dumps(header or {}), title, ex["id"])
            return ex["id"], False
        cid = self.x("INSERT INTO cases(person_id, site, case_number, court, url, title, header_json, party_name, dob_shown, first_seen, last_seen, last_change) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", person_id, site, case_number, court, url, title, json.dumps(header or {}), party_name, dob_shown, t, t, t)
        return cid, True
    def add_entries(self, case_id, entries, snapshot, url):
        """Insert entries not seen before. Returns the list of NEW entry rows (dicts)."""
        new = []
        t = now()
        for e in entries:
            k = e.key(str(case_id), "")
            snap = getattr(e, "snapshot", None) or snapshot
            src = getattr(e, "source_url", "") or url
            try:
                with _lock:
                    self.con.execute("INSERT INTO entries(case_id, entry_key, kind, date, text, fields_json, first_seen, snapshot_sha, snapshot_path, source_url) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                     (case_id, k, e.kind, e.date, e.text, json.dumps(e.fields, ensure_ascii=False), t, snap.sha256, snap.path, src))
                    self.con.commit()
                new.append({"kind": e.kind, "date": e.date, "text": e.text, "fields": e.fields, "entry_key": k})
            except sqlite3.IntegrityError:
                pass
        if new:
            self.x("UPDATE cases SET last_change=? WHERE id=?", t, case_id)
        return new
    def set_case_fetch(self, case_id, status, error=""):
        self.x("UPDATE cases SET last_fetch_status=?, last_fetch_error=? WHERE id=?", status, error[:1000], case_id)
    def cases_for(self, person_id):
        return self.q("SELECT * FROM cases WHERE person_id=? ORDER BY site, case_number", person_id)
    def entries_for(self, case_id):
        return self.q("SELECT * FROM entries WHERE case_id=? ORDER BY kind, date, id", case_id)
    # ---- runs / results / alerts
    def start_run(self, trigger):
        return self.x("INSERT INTO runs(started_at, trigger_) VALUES (?,?)", now(), trigger)
    def finish_run(self, run_id, summary):
        self.x("UPDATE runs SET finished_at=?, summary_json=? WHERE id=?", now(), json.dumps(summary), run_id)
    def result(self, run_id, person_id, site, status, found=0, new_items=0, message="", detail=None):
        self.x("INSERT INTO run_results(run_id, person_id, site, status, found, new_items, message, at, detail_json) VALUES (?,?,?,?,?,?,?,?,?)",
               run_id, person_id, site, status, found, new_items, message[:2000], now(), json.dumps(detail or {}))
    def alert(self, person_id, site, kind, text, case_number="", url=""):
        self.x("INSERT INTO alerts(created_at, person_id, site, case_number, kind, text, url) VALUES (?,?,?,?,?,?,?)",
               now(), person_id, site, case_number, kind, text[:4000], url)
    def snapshot(self, s):
        self.x("INSERT INTO snapshots(site, key, url, fetched_at, sha256, path, bytes) VALUES (?,?,?,?,?,?,?)",
               s.site, s.key, s.url, s.fetched_at, s.sha256, s.path, s.bytes)
    def custody(self, person_id, source, in_custody, bond_raw="", booking_no="", details="", snapshot_path="", url=""):
        self.x("INSERT INTO custody_status(person_id, source, in_custody, bond_raw, booking_no, details, seen_at, snapshot_path, url) VALUES (?,?,?,?,?,?,?,?,?)",
               person_id, source, 1 if in_custody else 0, bond_raw, booking_no, details[:2000], now(), snapshot_path, url)
    def badges(self, person_id):
        """warrant: latest DPS check found; new_info: unread non-baseline alerts; custody: latest custody row per source that says in custody."""
        b = {"warrant": False, "new_info": 0, "custody": []}
        w = self.one("SELECT status FROM warrant_checks WHERE person_id=? ORDER BY id DESC LIMIT 1", person_id)
        b["warrant"] = bool(w and w["status"] == "found")
        b["new_info"] = self.one("SELECT COUNT(*) c FROM alerts WHERE person_id=? AND seen=0 AND kind<>'baseline_case'", person_id)["c"]
        for src in [r["source"] for r in self.q("SELECT DISTINCT source FROM custody_status WHERE person_id=?", person_id)]:
            c = self.one("SELECT * FROM custody_status WHERE person_id=? AND source=? ORDER BY id DESC LIMIT 1", person_id, src)
            if c and c["in_custody"]:
                b["custody"].append(dict(c))
        return b
    def setting(self, key, default=""):
        r = self.one("SELECT value FROM settings WHERE key=?", key); return r["value"] if r else default
    def set_setting(self, key, value):
        self.x("INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", key, value)
