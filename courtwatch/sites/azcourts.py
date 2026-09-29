"""
Arizona Judicial Branch Public Access case lookup  (apps.azcourts.gov/publicaccess/caselookup.aspx)

Verified 2026-09-28: the page is gated by an image CAPTCHA (BotDetect: CaptchaCodeTextBox + "Submit") before
the name/case search form is shown. That is a human-only check, so this site is NOT searched automatically.
It is handled as a manual step: the person runs the search in their own browser and saves the results page
("Save page as... Webpage, Complete" or prints to PDF) and uploads it on the Import tab; this module parses
whatever tables that saved page contains, verbatim.
"""
import re
from .base import soup, text_of, html_tables, Entry, pairs_text, first_date

SITE = "azcourts"
URL = "https://apps.azcourts.gov/publicaccess/caselookup.aspx"
MANUAL_REASON = "image CAPTCHA (human-only) gates the search form"

CASE_NO_RE = re.compile(r"\b([A-Z]{1,3}\s?-?\s?\d{4,}[A-Z0-9-]*)\b")

def parse_saved(body: bytes):
    """Generic table reader for a saved results/case page. Returns list[Entry] and the page title."""
    sp = soup(body)
    title = text_of(sp.find("title"))
    entries = []
    for cap, hdr, rows in html_tables(sp):
        for r in rows:
            f = ({"Section": cap} | r) if cap else r
            entries.append(Entry("import_row", pairs_text(f), f, first_date(f)))
    return title, entries
