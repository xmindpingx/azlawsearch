"""
Maricopa County Superior Court public docket  (www.superiorcourt.maricopa.gov/docket/...)

Verified 2026-09-28 against live pages:
  search:  /docket/{Court}CourtCases/caseSearchResults.asp?lastName=..&FirstName=..&bName=
           rows = "Case Number" link + "Party Name" like "Doe, John - DOB: 1/1980"
  case:    /docket/{Court}CourtCases/caseInfo.asp?caseNumber=FC2024-000001
           Bootstrap grid: div.zebraRowTable blocks; each data row is label/value pairs where the
           label lives in a div.m-visibility.bold-font (or .bold-font in the header block).
           Blocks seen: Case Information, Party Information, Case Documents, Case Calendar,
           and on criminal cases Charges, Disposition, Sentence.
Plain GET works; no CAPTCHA on these pages.
"""
import re
from .base import Http, soup, text_of, norm_ws, CaseHit, CaseData, Entry, pairs_text, first_date

SITE = "superior"
BASE = "https://www.superiorcourt.maricopa.gov"
COURTS = {"family": "FamilyCourtCases", "criminal": "CriminalCourtCases", "civil": "CivilCourtCases", "probate": "ProbateCourtCases"}
PREFIX_TO_COURT = {"FC": "family", "FN": "family", "DR": "family", "CR": "criminal", "CV": "civil", "PB": "probate", "TX": "civil", "LC": "civil", "JS": "family"}
COURT_LABEL = {"family": "Maricopa County Superior Court - Family", "criminal": "Maricopa County Superior Court - Criminal",
               "civil": "Maricopa County Superior Court - Civil", "probate": "Maricopa County Superior Court - Probate"}

def court_for(case_number: str) -> str:
    return PREFIX_TO_COURT.get(case_number[:2].upper(), "civil")

def search_url(court: str, last: str, first: str) -> str:
    return f"{BASE}/docket/{COURTS[court]}/caseSearchResults.asp?lastName={last}&FirstName={first}&bName="

def case_url(court: str, case_number: str) -> str:
    return f"{BASE}/docket/{COURTS[court]}/caseInfo.asp?caseNumber={case_number}"

DOB_RE = re.compile(r"DOB:\s*([\d/]+)")

def _row_pairs(row):
    """label/value pairs from one grid row. Labels are bold-font divs; the next sibling div is the value."""
    fields, note = {}, ""
    divs = [d for d in row.find_all("div", recursive=False)]
    i = 0
    while i < len(divs):
        d = divs[i]; cls = d.get("class", [])
        if "bold-font" in cls or "m-visibility" in cls:
            label = text_of(d).rstrip(":").strip()
            val = text_of(divs[i + 1]) if i + 1 < len(divs) else ""
            if label:
                fields[label] = val
            i += 2; continue
        if d.find("b") and "NOTE" in text_of(d).upper():
            note = text_of(divs[i + 1]) if i + 1 < len(divs) else ""
            i += 2; continue
        i += 1
    if note:
        fields["NOTE"] = note
    return fields

def search(http: Http, court: str, last: str, first: str):
    url = search_url(court, last, first)
    body, snap = http.get(SITE, f"search:{court}:{last},{first}", url)
    sp = soup(body)
    hits = []
    for row in sp.select("div.zebraRowTable div.row"):
        a = row.find("a", href=re.compile(r"caseInfo\.asp\?caseNumber=", re.I))
        if not a:
            continue
        cn = text_of(a)
        f = _row_pairs(row)
        party = next((v for k, v in f.items() if k.lower().startswith("party name")), "")
        m = DOB_RE.search(party)
        hits.append(CaseHit(SITE, cn, BASE + a["href"], party_name=party, dob=m.group(1) if m else "",
                            court=COURT_LABEL[court], extra={"court_key": court}))
    return hits, snap

KIND_BY_TITLE = {"case documents": "docket", "case calendar": "calendar", "party information": "party", "charges": "charge",
                 "disposition": "disposition", "sentence": "sentence", "case information": "header"}

def case(http: Http, case_number: str, court: str = None) -> CaseData:
    court = court or court_for(case_number)
    url = case_url(court, case_number)
    body, snap = http.get(SITE, f"case:{case_number}", url)
    sp = soup(body)
    header, entries = {}, []
    blocks = sp.select("div.zebraRowTable")
    for b in blocks:
        rows = b.select(":scope > div.row")
        title = ""
        # section title: a lone col-12 row, or the nearest preceding heading text
        if rows and len(rows[0].find_all("div", recursive=False)) == 1 and "rptr-header" not in rows[0].get("class", []):
            title = text_of(rows[0]); rows = rows[1:]
        if not title:
            prev = b.find_previous(["h2", "h3", "h4", "h5", "div"], class_=re.compile("title|header|bold", re.I))
            title = text_of(prev) if prev else ""
        kind = "header" if b.get("id") == "tblForms" else ""
        if not kind:
            tl = title.lower()
            kind = next((v for k, v in KIND_BY_TITLE.items() if tl.startswith(k)), "")
        if not kind:
            kind = "party" if b.find(string=re.compile("Relationship")) else (title.lower().replace(" ", "_") or "other")
        for r in rows:
            if "rptr-header" in r.get("class", []):
                continue
            f = _row_pairs(r)
            if not f:
                continue
            if kind == "header":
                header.update(f); continue
            entries.append(Entry(kind, pairs_text(f), f, first_date(f)))
    if not blocks:
        # The docket serves an empty case body for cases it does not post online (e.g. protective-order matters):
        # keep the court's own wording as the only row so the run shows "found, no data online" rather than nothing.
        em = sp.select_one("p.emphasis")
        note = text_of(em) if em else "Case page returned no case data"
        entries.append(Entry("note", f"NO CASE DATA POSTED ONLINE: {note}", {"note": note}))
        header["Online status"] = "no case data posted on the public docket"
    court_label = COURT_LABEL[court]
    if header.get("Location"):
        court_label += f" ({header['Location']})"
    return CaseData(SITE, case_number, url, header, entries, snap, court=court_label)
