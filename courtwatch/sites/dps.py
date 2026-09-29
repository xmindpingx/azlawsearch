"""
Arizona DPS public warrant search  (www.azdps.gov/warrant-search)

Verified 2026-09-28: a Drupal "views" exposed form submitted by GET:
    /warrant-search?field_first_name_value=..&field_last_name_value=..&field_dob_value=MM/DD/YYYY
No CAPTCHA. The page says: first name, last name AND date of birth are required; max 5 results.
With no matching warrant the page shows the "view-empty" block ("Results Not Found ...").
The DOB box is a jQuery-UI datepicker with default options, whose format is mm/dd/yy => MM/DD/YYYY.
"""
from .base import Http, soup, text_of, html_tables, Entry, pairs_text, first_date

SITE = "dps"
URL = "https://www.azdps.gov/warrant-search"

def search_url(last, first, dob):
    return f"{URL}?field_first_name_value={first}&field_last_name_value={last}&field_dob_value={dob}"

def check(http: Http, last: str, first: str, dob: str):
    """Returns (status, entries, snapshot). status: 'found' | 'none' | 'unknown_layout'."""
    url = search_url(last, first, dob)
    body, snap = http.get(SITE, f"warrant:{last},{first},{dob}", url)
    sp = soup(body)
    entries = []
    content = sp.select_one(".view-content")
    if content:
        for cap, hdr, rows in html_tables(content):
            for r in rows:
                entries.append(Entry("warrant", pairs_text(r), r, first_date(r)))
        if not entries:
            for row in content.select(".views-row"):
                t = text_of(row)
                if t:
                    entries.append(Entry("warrant", t, {"text": t}))
        if not entries:
            t = text_of(content)
            if t:
                entries.append(Entry("warrant", t, {"text": t}))
        return "found", entries, snap
    empty = sp.select_one(".view-empty")
    if empty:
        return "none", [Entry("note", text_of(empty), {"text": text_of(empty)})], snap
    return "unknown_layout", [], snap
