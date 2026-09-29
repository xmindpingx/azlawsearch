"""
Shared plumbing for every site adapter.

Every adapter exposes some of:
    search(last, first, dob) -> list[CaseHit]       name search on that site
    case(case_number, ...)   -> CaseData            one case, fully parsed
    bulk()                   -> BulkData            whole-list sources (jail roster, CSV dump)
and always records the exact bytes it fetched (Snapshot) so every parsed row can be traced to a
SHA-256'd copy of the page it came from. Nothing is inferred: a field the page does not show is
left out (the EDISON exporter turns absent fields into MISSING_DATA).
"""
import dataclasses, datetime, hashlib, json, os, re, pathlib, time
import requests
from bs4 import BeautifulSoup

DATA = pathlib.Path(os.environ.get("CW_DATA", "/data/court_watch"))
SNAP = DATA / "snapshots"
UA = os.environ.get("CW_USER_AGENT", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TIMEOUT = int(os.environ.get("CW_HTTP_TIMEOUT", "90"))

def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")[:120] or "x"

def norm_ws(s) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()

def text_of(node) -> str:
    return norm_ws(node.get_text(" ", strip=True)) if node is not None else ""

@dataclasses.dataclass
class Snapshot:
    site: str
    key: str            # what was fetched, e.g. "case:FC2024-000001" or "search:doe,john"
    url: str
    fetched_at: str
    sha256: str
    path: str           # file on disk holding the exact bytes
    bytes: int
    content_type: str = ""
    def as_dict(self):
        return dataclasses.asdict(self)

def save_snapshot(site: str, key: str, url: str, body: bytes, content_type: str = "", ext: str = "html") -> Snapshot:
    """Write the exact bytes fetched to disk (never modified) and return its provenance."""
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    d = SNAP / site / slug(key); d.mkdir(parents=True, exist_ok=True)
    digest = sha256_bytes(body)
    p = d / f"{ts}_{digest[:12]}.{ext}"
    if not p.exists():
        p.write_bytes(body)
    return Snapshot(site, key, url, now_iso(), digest, str(p), len(body), content_type)

class Http:
    """requests.Session with the browser UA, cookie persistence within a run, and snapshotting."""
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    def get(self, site, key, url, ext="html", **kw) -> tuple[bytes, Snapshot]:
        r = self.s.get(url, timeout=TIMEOUT, **kw)
        r.raise_for_status()
        return r.content, save_snapshot(site, key, r.url, r.content, r.headers.get("Content-Type", ""), ext)
    def post(self, site, key, url, data, ext="html", **kw) -> tuple[bytes, Snapshot]:
        r = self.s.post(url, data=data, timeout=TIMEOUT, **kw)
        r.raise_for_status()
        return r.content, save_snapshot(site, key, r.url, r.content, r.headers.get("Content-Type", ""), ext)

def soup(body: bytes) -> BeautifulSoup:
    return BeautifulSoup(body, "lxml")

# ---------- generic case-page model ----------------------------------------------------------

@dataclasses.dataclass
class CaseHit:
    site: str
    case_number: str
    url: str
    party_name: str = ""
    dob: str = ""            # exactly as the page shows it (may be month/year only)
    court: str = ""
    extra: dict = dataclasses.field(default_factory=dict)
    def as_dict(self):
        return dataclasses.asdict(self)

@dataclasses.dataclass
class Entry:
    """One row of a case page: a docket entry, a hearing, a charge, a disposition, a payment ..."""
    kind: str                # docket | calendar | party | charge | disposition | sentence | event | payment | header | note
    text: str                # verbatim row text (label: value pairs joined), never edited
    fields: dict             # label -> value exactly as shown
    date: str = ""           # the row's own date as shown, if it has one
    snapshot: object = None  # per-row provenance when a case spans several pages (Mesa); else the case snapshot
    source_url: str = ""
    def key(self, case_number: str, site: str) -> str:
        return hashlib.sha256(f"{site}|{case_number}|{self.kind}|{self.text}".encode()).hexdigest()[:24]
    def as_dict(self):
        d = {"kind": self.kind, "text": self.text, "fields": self.fields, "date": self.date, "source_url": self.source_url}
        if self.snapshot is not None:
            d["snapshot"] = self.snapshot.as_dict()
        return d

@dataclasses.dataclass
class CaseData:
    site: str
    case_number: str
    url: str
    header: dict                      # Case Number / Judge / File Date / ... as shown
    entries: list                     # list[Entry]
    snapshot: Snapshot
    court: str = ""
    def as_dict(self):
        return {"site": self.site, "case_number": self.case_number, "url": self.url, "court": self.court,
                "header": self.header, "entries": [e.as_dict() for e in self.entries], "snapshot": self.snapshot.as_dict()}

def pairs_text(fields: dict) -> str:
    return " | ".join(f"{k}: {v}" for k, v in fields.items() if v != "")

DATE_IN_TEXT = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b")

def first_date(fields: dict, prefer=("Filing Date", "Date", "Docket Date", "Event Date", "Crime Date", "Disposition Date", "Payment Date", "Due Date")) -> str:
    for k in prefer:
        for fk, fv in fields.items():
            if fk.strip().rstrip(":").lower() == k.lower():
                m = DATE_IN_TEXT.search(fv or "")
                if m:
                    return m.group(1)
    for fv in fields.values():
        m = DATE_IN_TEXT.search(fv or "")
        if m:
            return m.group(1)
    return ""

def mdy_to_iso(s: str) -> str:
    """'9/16/2026' -> '2026-09-16'. Anything else -> '' (caller writes MISSING_DATA)."""
    m = re.match(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})", s or "")
    if not m:
        return ""
    mo, d, y = map(int, m.groups())
    try:
        return datetime.date(y, mo, d).isoformat()
    except ValueError:
        return ""

def html_tables(sp: BeautifulSoup):
    """Generic <table> reader: yields (caption/preceding heading, header list, rows as dicts)."""
    for t in sp.find_all("table"):
        rows = t.find_all("tr")
        if not rows:
            continue
        hdr = [text_of(c) for c in rows[0].find_all(["th", "td"])]
        if not any(hdr):
            continue
        cap = text_of(t.find("caption")) or ""
        if not cap:
            prev = t.find_previous(["h1", "h2", "h3", "h4", "h5", "h6", "caption", "b", "strong"])
            cap = text_of(prev) if prev else ""
        out = []
        for r in rows[1:]:
            cells = [text_of(c) for c in r.find_all(["td", "th"])]
            if not any(cells):
                continue
            d = {}
            for i, c in enumerate(cells):
                d[hdr[i] if i < len(hdr) and hdr[i] else f"col{i+1}"] = c
            out.append(d)
        yield cap, hdr, out
