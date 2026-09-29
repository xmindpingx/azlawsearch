"""
Arizona Department of Corrections, Rehabilitation & Reentry (ADCRR) inmate data search.
inmatedatasearch.azcorrections.gov is the real search app; corrections.az.gov/inmate-data-search
just iframes it.

Verified 2026-09-28 in headless Chromium. Plain curl/requests gets HTTP 403 on both URLs (Cloudflare
JS challenge); a headless browser passes through fine, and there is NO CAPTCHA anywhere on the actual
search form -- unlike mcso.org and AZ Courts public access, this site is fully automatable.

  Entry point: https://inmatedatasearch.azcorrections.gov/
  #btnSearchName switches the page to the by-name form:
    #txtLName    Last Name
    #txtFName    FIRST INITIAL ONLY (one letter -- not a full first-name field)
    rblGender    #rblGender_0 = Male, #rblGender_1 = Female
    rblStaus     #rblStaus_0 = Active, #rblStaus_1 = Inactive   (the site's own spelling)
    #btnName     submit
  Classic ASP.NET WebForms full-postback form: status is single-select, so covering both Active and
  Inactive for one person means submitting the form twice.

  Results: <table id="gvInmate">, columns [#, ADC#(link), Photo, Last Name, "First Name,MI", Admitted].
  The ADC# cell is a __doPostBack link ("...LinkNumber...") that redraws the SAME page as a detail
  view -- there is no separate URL per inmate and no way to hold two searches open in one page state,
  so seeing more than one hit for a given name/gender/status means re-running the search once per hit.

  Detail view -- all simple "header row + one data row" GridViews except the two history tables:
    GridView7       Last Name / First Name / Middle Initial
    GridView8       Age / Gender / Height (in) / Weight / Hair Color
    GridView9       Eye Color / Ethnic Origin / Custody Class / Admission
    GridView11      Projected Eligible Release Date / Prison Release Date / Release Type
    GridView12      Complex / Unit / Last Movement / Status              ("Most Recent Location")
    GVCommitment    one row per sentence count: Commit# / Sentence Length / Sentence County /
                    Court Cause# / Offense Date / Sentence Date / Sentence Status / Crime
    GVProfileClass  many historical rows: Complete Date / Classification Type / Custody Risk / Internal Risk

  This is a STATE PRISON system, not a pretrial jail, so there is no bond amount anywhere on the site.
  Only incarceration status (Active/Inactive) and sentence detail apply here -- callers must never
  attach a bond figure to an ADCRR hit.

  The search form has no date-of-birth field anywhere, on the search or the detail view. A hit can
  therefore never be confirmed against a person's DOB by the site itself -- only Age (detail view
  only) gives even a rough sanity check, and only once a specific record is already open. Every hit
  from this adapter is a name + first-initial + gender match ONLY and must be treated as unconfirmed,
  never asserted to be the same individual, per the no-fabrication policy.
"""
import datetime, re, time
from .base import save_snapshot, CaseHit, CaseData, Entry, pairs_text
from . import browser

SITE = "adcrr"
BASE = "https://inmatedatasearch.azcorrections.gov/"
LABEL = "AZ Dept. of Corrections inmate search (state prison)"
GENDERS = [("0", "Male"), ("1", "Female")]
STATUSES = [("0", "Active"), ("1", "Inactive")]


def _rows(pg, selector):
    if not pg.query_selector(selector):
        return []
    return pg.eval_on_selector_all(
        f"{selector} tr",
        "rs => rs.map(r => Array.from(r.querySelectorAll('th,td')).map(c => c.innerText.trim().replace(/\\s+/g,' ')))")


def _snap(pg, key):
    return save_snapshot(SITE, key, pg.url, pg.content().encode("utf-8"), "text/html")


def _kv(pg, table_id):
    """header row + one data row -> dict. Used for the single-record GridViews."""
    r = _rows(pg, f"#{table_id}")
    if len(r) < 2:
        return {}
    hdr, val = r[0], r[1]
    return {hdr[i]: val[i] for i in range(min(len(hdr), len(val))) if hdr[i]}


def _multi(pg, table_id):
    """header row + N data rows -> list[dict]. Used for GVCommitment / GVProfileClass."""
    r = _rows(pg, f"#{table_id}")
    if len(r) < 2:
        return []
    hdr = r[0]
    return [{hdr[i]: row[i] for i in range(min(len(hdr), len(row))) if hdr[i]} for row in r[1:] if any(row)]


def _do_search(pg, last, first_initial, gender, status):
    pg.goto(BASE, wait_until="domcontentloaded", timeout=90000)
    pg.click("#btnSearchName")
    pg.fill("#txtLName", last)
    pg.fill("#txtFName", (first_initial or "")[:1])
    pg.check(f"#rblGender_{gender}")
    pg.check(f"#rblStaus_{status}")
    pg.click("#btnName")
    pg.wait_for_load_state("load", timeout=90000)
    time.sleep(1.5)


def _result_rows(pg):
    out = []
    for r in _rows(pg, "#gvInmate")[1:]:
        if len(r) < 6 or not re.fullmatch(r"\d{4,}", r[1]):
            continue
        out.append({"adc": r[1], "last": r[3], "first_mi": r[4], "admitted": r[5]})
    return out


def _detail_links(pg):
    """Only the ADC#/'LinkNumber' postback links -- excludes any pager links a large result set might add."""
    return [a for a in pg.query_selector_all("#gvInmate a") if "LinkNumber" in (a.get_attribute("href") or "")]


def _detail(pg) -> dict:
    d = {}
    d.update(_kv(pg, "GridView7"))
    d.update(_kv(pg, "GridView8"))
    d.update(_kv(pg, "GridView9"))
    d.update(_kv(pg, "GridView11"))
    d.update(_kv(pg, "GridView12"))
    d["_commitments"] = _multi(pg, "GVCommitment")
    d["_classifications"] = _multi(pg, "GVProfileClass")
    return d


def age_sanity(dob_mmddyyyy: str, age_shown: str):
    """
    A sanity check only, never a match decision: given the person's own stated DOB (MM/DD/YYYY) and
    the Age ADCRR shows for a hit, say whether that age is CONSISTENT or INCONSISTENT with the DOB,
    using the real date this code runs. Returns None (cannot assess) if either input is missing or
    unparseable -- this never guesses.
    """
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", (dob_mmddyyyy or "").strip())
    if not m or not re.fullmatch(r"\d{1,3}", (age_shown or "").strip()):
        return None
    mo, d, y = map(int, m.groups())
    try:
        dob = datetime.date(y, mo, d)
    except ValueError:
        return None
    today = datetime.date.today()
    expected = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    shown = int(age_shown.strip())
    return {"expected_age": expected, "age_shown": shown, "consistent": abs(expected - shown) <= 1}


def search_person(last, first_initial, gender=None, statuses=("0", "1"), person_dob=""):
    """
    gender: "0" Male, "1" Female, or None/"" to run both (the form requires a value; every hit from an
    unconfirmed-gender run is flagged in its header/extra). statuses: which of Active("0")/Inactive("1")
    to run; default both.
    Returns (pairs, snaps): pairs is list[(CaseHit, CaseData)], one per ADCRR record found; snaps is
    every Snapshot fetched, in order.
    """
    genders = [gender] if gender in ("0", "1") else [g for g, _ in GENDERS]
    pairs, snaps = [], []
    with browser.page() as pg:
        for g in genders:
            for st in statuses:
                _do_search(pg, last, first_initial, g, st)
                snaps.append(_snap(pg, f"search:{last},{first_initial},{dict(GENDERS)[g]},{dict(STATUSES)[st]}"))
                rows = _result_rows(pg)
                for i in range(len(rows)):
                    if i > 0:
                        _do_search(pg, last, first_initial, g, st)  # fresh grid before each detail click
                    links = _detail_links(pg)
                    if i >= len(links):
                        continue
                    links[i].click()
                    pg.wait_for_load_state("load", timeout=90000)
                    time.sleep(1.5)
                    snap_detail = _snap(pg, f"detail:{rows[i]['adc']}")
                    snaps.append(snap_detail)
                    det = _detail(pg)
                    header = {k: v for k, v in det.items() if not k.startswith("_")}
                    header["Status searched"] = dict(STATUSES)[st]
                    gender_note = dict(GENDERS)[g]
                    if gender not in ("0", "1"):
                        gender_note += " (gender unconfirmed - not on file, both searched)"
                    header["Gender searched"] = gender_note
                    sanity = age_sanity(person_dob, header.get("Age", ""))
                    if sanity is not None:
                        header["Age vs. stated DOB"] = ("CONSISTENT" if sanity["consistent"] else "INCONSISTENT") + \
                            f" (DOB implies ~{sanity['expected_age']}, ADCRR shows {sanity['age_shown']})"
                    else:
                        header["Age vs. stated DOB"] = "MISSING_DATA (no DOB on file, or age not shown on this record)"
                    entries = []
                    for c in det["_commitments"]:
                        entries.append(Entry("sentence", pairs_text(c), c, c.get("Sentence Date", ""), snap_detail, pg.url))
                    for cl in det["_classifications"]:
                        entries.append(Entry("note", pairs_text(cl), cl, cl.get("Complete Date", ""), snap_detail, pg.url))
                    hit = CaseHit(SITE, rows[i]["adc"], BASE, party_name=f"{rows[i]['last']}, {rows[i]['first_mi']}", dob="",
                                  court=LABEL, extra={"status": dict(STATUSES)[st], "gender_searched": gender_note,
                                                       "admitted": rows[i]["admitted"]})
                    cd = CaseData(SITE, rows[i]["adc"], BASE, header, entries, snap_detail, court=LABEL)
                    pairs.append((hit, cd))
    return pairs, snaps
