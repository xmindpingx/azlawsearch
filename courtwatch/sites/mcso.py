"""
portal.mobileso.com/mcso/jail/jp_ci_c.asp -- NOTE: this is the MOBILE COUNTY SHERIFF'S OFFICE (Alabama) daily jail roster,
not Maricopa County. Kept because it was requested; labeled as such everywhere. The Maricopa County (AZ) inmate lookup is
www.mcso.org/InmateInfo and is gated by a reCAPTCHA checkbox, so it is a manual Import, not an automated pull.

Verified 2026-09-28: one large HTML table, "Daily Jail Population Report-Alpha List-Charges as of M/D/YYYY".
Rows <tr class="ctd"|"cdt">: Booking # (link to jinmate.asp?qs_mni_id=..&qs_bno=..), Name "LAST,FIRST MIDDLE",
Race, Sex, DOB MM/DD/YYYY, Status, Age, Booking Date, Arrival Date, Expected Release Date.
Plain GET, no CAPTCHA. ~700 KB per fetch.
"""
import re
from .base import Http, soup, text_of

SITE = "mcso"
URL = "https://portal.mobileso.com/mcso/jail/jp_ci_c.asp"
ROSTER_LABEL = "Mobile County (AL) jail roster"
MARICOPA_INMATE_URL = "https://www.mcso.org/InmateInfo"
COLS = ["booking_no", "name", "race", "sex", "dob", "status", "age", "booking_date", "arrival_date", "expected_release"]

def fetch(http: Http):
    body, snap = http.get(SITE, "roster", URL)
    sp = soup(body)
    title = ""
    t = sp.find("td", class_="fhd")
    if t:
        title = text_of(t)
    rows = []
    for tr in sp.find_all("tr", class_=re.compile(r"^(ctd|cdt)$")):
        cells = tr.find_all("td")
        if len(cells) < 8:
            continue
        vals = [text_of(c) for c in cells]
        rec = dict(zip(COLS, vals + [""] * (len(COLS) - len(vals))))
        a = cells[0].find("a", href=True)
        rec["detail_url"] = "https://portal.mobileso.com/mcso/jail/" + a["href"] if a else ""
        rows.append(rec)
    return {"title": title, "rows": rows, "snapshot": snap}

def name_matches(row: dict, last: str, first: str, dob: str = "") -> bool:
    """Roster name is 'LAST,FIRST MIDDLE'. Match on last + first token; if a DOB is known, it must match exactly."""
    n = row.get("name", "").upper()
    if "," not in n:
        return False
    rl, rf = n.split(",", 1)
    if rl.strip() != last.strip().upper():
        return False
    if not rf.strip().startswith(first.strip().upper()):
        return False
    if dob:
        return row.get("dob", "") == dob
    return True
