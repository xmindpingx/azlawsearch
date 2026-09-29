"""
Mesa Municipal Court eCourt  (ecourt.mesaaz.gov)

Verified 2026-09-28 in headless Chromium:
  home "/"           ASP.NET WebForms; name search = #txtFirstNameSearch #txtLastNameSearch #txtDateOfBirth (MM/DD/YYYY)
                     + #btnSearchTicket. The page's own script fills #captchatoken (reCAPTCHA v3) on load.
  /SearchResults     <table id="PageBody_gvCaseSearchResults">: Case Options (link cn=..), Defendant Name, DOB (MM/YYYY), City, State
  /CaseMainMenu?cn=  three postback buttons: btnPaymentInquiry, btnEventInquiry, btnDispositionReport
  /PaymentInquiry    table "Last N Payments" (date, amount); table with Payment #/Amount Due/Due Date/Case Balance Owing/
                     Total Contract Amount (or just "Current Balance"); "Make Payment" button if eligible
  /EventInquiry      "Next Court Date" text + table gvPastEvents (Sessions & Motions, Date, Result, Result Date)
  /DispositionReport Case Information (Name, Case Type, Case Number, Report Date), gvViolations rows
                     ("Violation Code: .. Description: .. Date of Violation: .. Date of Disposition: .. Disposition: .."),
                     gvNextScheduledDate, "Sentencing Information Current Balance: $x  Payments are current"
All case pages need the server-side session that the search creates, so one browser session does search + cases.
"""
import re, time
from .base import save_snapshot, norm_ws, CaseHit, CaseData, Entry, pairs_text, first_date
from . import browser

SITE = "mesa"
BASE = "https://ecourt.mesaaz.gov/"
COURT_LABEL = "Mesa Municipal Court"

def _table_rows(pg, selector):
    return pg.eval_on_selector_all(f"{selector} tr", "rs => rs.map(r => Array.from(r.querySelectorAll('th,td')).map(c => c.innerText.trim().replace(/\\s+/g,' ')))")

def _body_text(pg):
    return norm_ws(pg.evaluate("() => document.body.innerText"))

def _snap(pg, key):
    html = pg.content().encode("utf-8")
    return save_snapshot(SITE, key, pg.url, html, "text/html")

VIOL_RE = re.compile(r"Violation Code:\s*(?P<code>.*?)\s+Description:\s*(?P<desc>.*?)\s+Date of Violation:\s*(?P<vdate>[\d/]+)\s*(?:Date of Disposition:\s*(?P<ddate>[\d/]*))?\s*(?:Disposition:\s*(?P<disp>.*))?$")
LONG_DATE_RE = re.compile(r"\b([A-Z][a-z]+ \d{1,2}, \d{4})\b")

class Session:
    def __init__(self, last, first, dob=""):
        self.last, self.first, self.dob = last, first, dob
        self.hits, self.search_snapshot = [], None
    def __enter__(self):
        self._cm = browser.page(); self.pg = self._cm.__enter__()
        pg = self.pg
        pg.goto(BASE, wait_until="domcontentloaded", timeout=90000)
        for _ in range(40):                       # the page's script fills the token; wait for it
            if pg.eval_on_selector("#captchatoken", "e => e.value"):
                break
            time.sleep(1)
        pg.fill("#txtFirstNameSearch", self.first); pg.fill("#txtLastNameSearch", self.last)
        if self.dob:
            pg.fill("#txtDateOfBirth", self.dob)
        pg.click("#btnSearchTicket"); pg.wait_for_load_state("load", timeout=90000); time.sleep(2)
        self.search_snapshot = _snap(pg, f"search:{self.last},{self.first},{self.dob}")
        if "SearchResults" not in pg.url:
            msg = _body_text(pg)
            m = re.search(r"(Please refresh the page[^.]*\.|No (?:records|cases) found[^.]*\.?|not found[^.]*\.)", msg, re.I)
            if m and "refresh" in m.group(1).lower():
                raise RuntimeError("Mesa eCourt rejected the search (captcha message): " + m.group(1))
            self.hits = []
            return self
        rows = _table_rows(pg, "#PageBody_gvCaseSearchResults")
        for r in rows[1:]:
            if len(r) >= 3 and re.fullmatch(r"\d{6,}", r[0]):
                self.hits.append(CaseHit(SITE, r[0], f"{BASE}CaseMainMenu?cn={r[0]}", party_name=r[1], dob=r[2], court=COURT_LABEL,
                                         extra={"city": r[3] if len(r) > 3 else "", "state": r[4] if len(r) > 4 else ""}))
        return self
    def __exit__(self, *a):
        return self._cm.__exit__(*a)

    def _menu(self, cn):
        self.pg.goto(f"{BASE}CaseMainMenu?cn={cn}", wait_until="domcontentloaded", timeout=90000); time.sleep(1)
        if "SessionExpired" in self.pg.url:
            raise RuntimeError("Mesa session expired before case menu")
    def _click(self, name):
        self.pg.click(f"input[name='{name}']"); self.pg.wait_for_load_state("load", timeout=90000); time.sleep(1.5)

    def case(self, cn) -> CaseData:
        pg = self.pg
        header, entries = {"Case Number": cn}, []
        # -- payments / balance
        self._menu(cn); self._click("ctl00$PageBody$btnPaymentInquiry")
        snap_pay = _snap(pg, f"case:{cn}:payment"); url_pay = pg.url
        for r in _table_rows(pg, "#PageBody_gvPaymentHistory"):
            if len(r) == 2 and re.search(r"\d/\d", r[0]):
                f = {"Payment Date": r[0], "Amount": r[1]}
                entries.append(Entry("payment", pairs_text(f), f, first_date(f), snap_pay, url_pay))
        inst = {}
        for r in _table_rows(pg, "table:not(#PageBody_gvPaymentHistory)"):
            if len(r) == 2 and r[0] and not re.search(r"\d/\d", r[0]):
                inst[r[0].rstrip(":")] = r[1]
        if inst:
            entries.append(Entry("balance", pairs_text(inst), inst, first_date(inst, ("Due Date",)), snap_pay, url_pay))
        if pg.query_selector("input[name='ctl00$PageBody$btnMakePayment']"):
            header["Online payment"] = "Make Payment button available on PaymentInquiry"
        # -- events
        self._menu(cn); self._click("ctl00$PageBody$btnEventInquiry")
        snap_ev = _snap(pg, f"case:{cn}:events"); url_ev = pg.url
        txt = _body_text(pg)
        m = re.search(r"Next Court Date\s*(.*?)\s*Previous Court Dates", txt)
        if m:
            f = {"Next Court Date": m.group(1).strip()}
            entries.append(Entry("event", pairs_text(f), f, "", snap_ev, url_ev))
        rows = _table_rows(pg, "#PageBody_gvPastEvents")
        hdr = rows[0] if rows else ["Sessions & Motions", "Date", "Result", "Result Date"]
        for r in rows[1:]:
            f = {hdr[i] if i < len(hdr) else f"col{i+1}": v for i, v in enumerate(r)}
            entries.append(Entry("event", pairs_text(f), f, _long_date(f.get("Date", "")), snap_ev, url_ev))
        # -- disposition report
        self._menu(cn); self._click("ctl00$PageBody$btnDispositionReport")
        snap_d = _snap(pg, f"case:{cn}:disposition"); url_d = pg.url
        txt = _body_text(pg)
        for lab in ("Name", "Case Type", "Case Number", "Report Date"):
            m = re.search(lab + r":\s*(.*?)\s+(?=Name:|Case Type:|Case Number:|Report Date:|Violations)", txt)
            if m:
                header[lab] = m.group(1).strip()
        for r in _table_rows(pg, "#PageBody_gvViolations"):
            if not r:
                continue
            m = VIOL_RE.match(r[0])
            f = {k: (v or "") for k, v in m.groupdict().items()} if m else {"text": r[0]}
            f = {"Violation Code": f.get("code", ""), "Description": f.get("desc", ""), "Date of Violation": f.get("vdate", ""),
                 "Date of Disposition": f.get("ddate", ""), "Disposition": f.get("disp", "")} if m else f
            entries.append(Entry("disposition", pairs_text(f), f, f.get("Date of Disposition") or f.get("Date of Violation", ""), snap_d, url_d))
        for r in _table_rows(pg, "#PageBody_gvNextScheduledDate"):
            if r and r[0]:
                f = {"Next Scheduled Court Date": r[0]}
                entries.append(Entry("event", pairs_text(f), f, first_date(f), snap_d, url_d))
        m = re.search(r"Sentencing Information\s*(.*?)\s*(?:Home About Us|$)", txt)
        if m:
            f = {"Sentencing Information": m.group(1).strip()}
            entries.append(Entry("balance", pairs_text(f), f, "", snap_d, url_d))
        return CaseData(SITE, cn, f"{BASE}CaseMainMenu?cn={cn}", header, entries, snap_d, court=COURT_LABEL)

def _long_date(s):
    m = LONG_DATE_RE.search(s or "")
    if not m:
        return ""
    import datetime
    try:
        return datetime.datetime.strptime(m.group(1), "%B %d, %Y").strftime("%-m/%-d/%Y")
    except ValueError:
        return ""
