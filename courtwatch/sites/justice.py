"""
Maricopa County Justice Courts public records  (justicecourts.maricopa.gov/app/courtrecords/...)

Verified 2026-09-28 against live pages:
  search:  /app/courtrecords/caseSearchResults?lastName=..&FirstName=..&DOB=MM/DD/YYYY
           <table id="MainContent_CaseSearchResultsGridView"> rows: case link (CaseInfo.aspx?casenumber=JC2024000001000),
           party span, "DOB: 1/1980" span, and a hidden DobHiddenField holding the full DOB.
  case:    /app/courtrecords/CaseInfo.aspx?casenumber=<case+000>
           <section class="jc-case-information|jc-party-information|jc-disposition-information|jc-case-documents|jc-events">
           label/value pairs: div.jc-case-info-header + div.span3-4 ; repeated groups separated by blank rows.
Plain GET works (site is behind Cloudflare but served normally to a browser UA).
"""
import re
from .base import Http, soup, text_of, CaseHit, CaseData, Entry, pairs_text, first_date

SITE = "justice"
BASE = "https://justicecourts.maricopa.gov/app/courtrecords/"
COURT_LABEL = "Maricopa County Justice Courts"

def search_url(last, first, dob):
    return f"{BASE}caseSearchResults?lastName={last}&FirstName={first}&DOB={dob}"

def case_url(case_number):
    cn = case_number if len(case_number) > 12 else case_number + "000"
    return f"{BASE}CaseInfo.aspx?casenumber={cn}"

def search(http: Http, last: str, first: str, dob: str):
    url = search_url(last, first, dob)
    body, snap = http.get(SITE, f"search:{last},{first},{dob}", url)
    sp = soup(body)
    hits = []
    t = sp.find("table", id=re.compile("CaseSearchResultsGridView"))
    if t:
        for tr in t.find_all("tr")[1:]:
            a = tr.find("a", href=re.compile(r"CaseInfo\.aspx\?casenumber=", re.I))
            if not a:
                continue
            party = tr.find("span", id=re.compile("PartyLabel"))
            dobl = tr.find("span", id=re.compile("DateLabel"))
            hid = tr.find("input", id=re.compile("DobHiddenField"))
            hits.append(CaseHit(SITE, text_of(a), BASE + a["href"], party_name=text_of(party), dob=text_of(dobl),
                                court=COURT_LABEL, extra={"dob_full": hid.get("value", "") if hid else ""}))
    return hits, snap

SECTION_KIND = {"jc-case-information": "header", "jc-party-information": "party", "jc-disposition-information": "disposition",
                "jc-case-documents": "docket", "jc-events": "event", "jc-financial": "payment", "jc-sentence": "sentence"}

def _pairs(container):
    """label/value pairs inside one group container, splitting when a label repeats."""
    groups, cur, subtitle = [], {}, ""
    for el in container.find_all("div", recursive=True):
        cls = el.get("class", [])
        if "jc-column-title" in cls:                       # Plaintiff / Defendant sub-heading
            if cur:
                groups.append((subtitle, cur)); cur = {}
            subtitle = text_of(el); continue
        if "jc-case-info-header" in cls:
            label = text_of(el).rstrip(":").strip()
            val_el = el.find_next_sibling("div")
            val = text_of(val_el) if val_el else ""
            if label in cur:
                groups.append((subtitle, cur)); cur = {}
            cur[label] = val
    if cur:
        groups.append((subtitle, cur))
    return groups

GROUP_WRAPPERS = ["jc-case-disposition-wrapper", "jc-case-party-info", "jc-case-events-wrapper", "jc-case-documents-wrapper",
                  "jc-case-event-wrapper", "jc-case-document-wrapper"]

def _groups(section):
    """Groups of label/value pairs: one per wrapper div when the section has them, else split on repeated labels."""
    wrappers = [d for d in section.find_all("div", recursive=True) if any(c in GROUP_WRAPPERS for c in d.get("class", []))]
    # keep only outermost wrappers
    wrappers = [w for w in wrappers if not any(p is not w and p in wrappers for p in w.parents)]
    if not wrappers:
        return _pairs(section)
    out = []
    for w in wrappers:
        out.extend(_pairs(w))
    return out

def case(http: Http, case_number: str) -> CaseData:
    url = case_url(case_number)
    body, snap = http.get(SITE, f"case:{case_number}", url)
    sp = soup(body)
    header, entries = {}, []
    for sec in sp.find_all("section"):
        cls = [c for c in sec.get("class", []) if c.startswith("jc-")]
        key = next((c for c in cls if c in SECTION_KIND), None)
        if not key:
            continue
        kind = SECTION_KIND[key]
        title = text_of(sec.find("h5")) or key
        groups = _groups(sec)
        if kind == "header":
            for _, g in groups:
                header.update(g)
            continue
        if not groups:
            txt = text_of(sec)
            txt = txt[len(title):].strip() if txt.startswith(title) else txt
            if txt:
                entries.append(Entry("note", f"{title}: {txt}", {title: txt}))
            continue
        for subtitle, g in groups:
            f = ({"Role": subtitle} | g) if subtitle else g
            entries.append(Entry(kind, pairs_text(f), f, first_date(f)))
    court = COURT_LABEL + (f" ({header['Location']})" if header.get("Location") else "")
    return CaseData(SITE, case_number.replace("000", "") if case_number.endswith("000") and len(case_number) == 15 else case_number,
                    url, header, entries, snap, court=court)
