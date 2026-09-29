#!/usr/bin/env python3
"""
EDISON Court Watch - web UI + 3x/day scheduler.
  WATCH_TIMES   "06:00,12:00,18:00"   local times (TZ) to run everything
  TZ            America/Phoenix
  RUN_ON_START  1 = run a full pass when the container starts
  CW_PIN        optional PIN for the page (defaults to UPLOAD_PIN); LAN only by design
"""
import os, io, re, json, threading, time, datetime, traceback, hashlib, pathlib, urllib.parse
from zoneinfo import ZoneInfo
from flask import Flask, request, redirect, render_template_string, flash, abort, send_file, make_response, url_for
from jinja2 import DictLoader, Environment
from courtwatch import templates as T
from courtwatch.db import Store, now
from courtwatch import watch
from courtwatch.sites import base, superior, justice, dps, azcourts
from courtwatch.sites.base import Http

TZ = ZoneInfo(os.environ.get("TZ", "America/Phoenix"))
TIMES = [t.strip() for t in os.environ.get("WATCH_TIMES", "06:00,12:00,18:00").split(",") if t.strip()]
PIN = os.environ.get("CW_PIN") or os.environ.get("UPLOAD_PIN", "")
POWERBI_URL = os.environ.get("WATCH_POWERBI_URL", "")

app = Flask(__name__)
app.secret_key = hashlib.sha256((PIN or "court-watch").encode()).hexdigest()
app.jinja_loader = DictLoader({"base": T.BASE})

LIVE_SITES = [("superior", "Superior Court (Family/Criminal/Civil/Probate)"), ("justice", "Justice Courts"), ("mesa", "Mesa eCourt"), ("dps", "DPS warrants")]
LOCAL_SITES = [("cases", "cases & docket rows"), ("mcso", "MCSO roster"), ("chandler", "Chandler bookings"), ("powerbi", "Power BI captures"), ("imports", "imported pages")]
SITE_LABELS = {k: v[0] for k, v in watch.SITES.items()}

# ---------------- background jobs ----------------
JOBS, JOBS_LOCK, _jid = {}, threading.Lock(), [0]
RUN_LOCK = threading.Lock()

class Job:
    def __init__(self, desc):
        with JOBS_LOCK:
            _jid[0] += 1; self.id = _jid[0]
        self.desc, self.status, self.lines, self.started, self.result_html = desc, "running", [], now(), ""
        JOBS[self.id] = self
    def log(self, *a):
        self.lines.append(" ".join(str(x) for x in a)); print(f"[job {self.id}]", *a, flush=True)
    @property
    def log_text(self):
        return "\n".join(self.lines[-400:])

def run_job(desc, fn):
    job = Job(desc)
    def _t():
        try:
            fn(job); job.status = "done"
        except Exception as e:
            job.log("ERROR", repr(e)); job.log(traceback.format_exc()[-1500:]); job.status = "failed"
    threading.Thread(target=_t, daemon=True).start()
    return job

def full_run(job, trigger="manual", people_ids=None, sites=None):
    if not RUN_LOCK.acquire(blocking=False):
        job.log("another run is already in progress; waiting for it"); RUN_LOCK.acquire()
    try:
        watch.run(trigger=trigger, people_ids=people_ids, sites=sites, log=job.log)
    finally:
        RUN_LOCK.release()

# ---------------- scheduler ----------------
def next_run_time(now_local=None):
    now_local = now_local or datetime.datetime.now(TZ)
    cands = []
    for d in (0, 1):
        day = (now_local + datetime.timedelta(days=d)).date()
        for t in TIMES:
            hh, mm = map(int, t.split(":"))
            c = datetime.datetime.combine(day, datetime.time(hh, mm), TZ)
            if c > now_local:
                cands.append(c)
    return min(cands) if cands else now_local + datetime.timedelta(hours=8)

def scheduler():
    if os.environ.get("RUN_ON_START", "1") == "1":
        time.sleep(20)
        last = store().one("SELECT finished_at FROM runs WHERE finished_at<>'' ORDER BY id DESC LIMIT 1")
        recent = False
        if last:
            try:
                recent = (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(last["finished_at"])).total_seconds() < 2 * 3600
            except Exception:
                recent = False
        if recent:
            print("[scheduler] startup run skipped: last full run finished under 2 hours ago", flush=True)
        else:
            run_job("startup run", lambda job: full_run(job, "startup"))
    while True:
        nxt = next_run_time()
        wait = (nxt - datetime.datetime.now(TZ)).total_seconds()
        print(f"[scheduler] next run at {nxt.isoformat()} (in {wait/60:.0f} min)", flush=True)
        time.sleep(max(30, wait))
        run_job(f"scheduled run {nxt.strftime('%Y-%m-%d %H:%M')}", lambda job: full_run(job, "scheduled"))
        time.sleep(90)

# ---------------- helpers ----------------
def store():
    if not hasattr(app, "_store"):
        app._store = Store()
    return app._store

def people_names():
    return {p["id"]: f"{p['last']}, {p['first']}" for p in store().people(active_only=False)}

def tabs():
    unread = store().one("SELECT COUNT(*) c FROM alerts WHERE seen=0")["c"]
    return [("", "Dashboard", unread), ("monitor", "Monitoring", 0), ("search", "Search", 0), ("payments", "Payments", 0),
            ("warrants", "Warrants", 0), ("arrests", "Arrests", 0), ("names", "Names", 0), ("import", "Import", 0)]

def page(tpl, tab, title, **ctx):
    return render_template_string(tpl, tabs=tabs(), tab=tab, title=title, people_names=people_names(), site_labels=SITE_LABELS, **ctx)

@app.before_request
def auth():
    if not PIN or request.path == "/login" or request.path.startswith("/static"):
        return
    if request.cookies.get("cwpin") != hashlib.sha256(PIN.encode()).hexdigest():
        return page(T.LOGIN, "", "Login") if request.method == "GET" else abort(401)

@app.post("/login")
def login():
    if request.form.get("pin", "") == PIN:
        r = make_response(redirect("/")); r.set_cookie("cwpin", hashlib.sha256(PIN.encode()).hexdigest(), max_age=90 * 86400, samesite="Lax"); return r
    flash("wrong PIN"); return redirect("/")

# ---------------- pages ----------------
@app.get("/")
def dash():
    s = store()
    last_run = s.one("SELECT * FROM runs ORDER BY id DESC LIMIT 1")
    latest = s.q("SELECT r.* FROM run_results r JOIN (SELECT person_id, site, MAX(id) mid FROM run_results GROUP BY person_id, site) m ON m.mid=r.id ORDER BY r.person_id, r.site")
    alerts = s.q("SELECT * FROM alerts WHERE seen=0 ORDER BY id DESC LIMIT 200")
    job = max(JOBS.values(), key=lambda j: j.id) if JOBS else None
    return page(T.DASH, "", "Dashboard", times=", ".join(TIMES), tz=str(TZ), next_run=next_run_time().strftime("%a %m/%d %H:%M"),
                last_run=last_run, latest=latest, alerts=alerts, job=job, people=[person_bundle(p) for p in s.people()])

@app.post("/alerts/seen")
def alerts_seen():
    store().x("UPDATE alerts SET seen=1 WHERE seen=0"); return redirect("/")

@app.post("/run")
def run_now():
    pid = request.form.get("person_id")
    ids = [int(pid)] if pid else None
    sites = request.form.getlist("sites") or None
    job = run_job("manual run" + (f" person {pid}" if pid else ""), lambda j: full_run(j, "manual", ids, sites))
    return redirect(f"/job/{job.id}")

@app.get("/job/<int:jid>")
def job_page(jid):
    j = JOBS.get(jid) or abort(404)
    return page(T.JOB, "", f"Job {jid}", job=type("J", (), {"id": j.id, "status": j.status, "desc": j.desc, "started": j.started, "log": j.log_text, "result_html": j.result_html}))

def person_bundle(p):
    """Everything captured for one person: latest per-site results, history, cases (+row counts), warrant checks, roster matches, alerts."""
    s = store(); d = dict(p)
    d["latest"] = s.q("SELECT r.* FROM run_results r JOIN (SELECT site, MAX(id) mid FROM run_results WHERE person_id=? GROUP BY site) m ON m.mid=r.id ORDER BY r.site", p["id"])
    d["history"] = s.q("SELECT * FROM run_results WHERE person_id=? ORDER BY id DESC LIMIT 30", p["id"])
    cases = []
    for c in s.cases_for(p["id"]):
        cd = dict(c); cd["rows"] = s.one("SELECT COUNT(*) c FROM entries WHERE case_id=?", c["id"])["c"]; cases.append(cd)
    d["cases"] = cases
    d["warrants"] = s.q("SELECT * FROM warrant_checks WHERE person_id=? ORDER BY id DESC LIMIT 10", p["id"])
    d["arrests"] = s.q("SELECT m.first_seen match_seen, r.* FROM mcso_matches m JOIN mcso_roster r ON r.booking_no=m.booking_no WHERE m.person_id=? ORDER BY m.id DESC", p["id"])
    d["alerts"] = s.q("SELECT * FROM alerts WHERE person_id=? ORDER BY id DESC LIMIT 40", p["id"])
    lu = s.one("SELECT MAX(e.first_seen) m FROM entries e JOIN cases c ON c.id=e.case_id WHERE c.person_id=?", p["id"])
    d["last_update"] = (lu["m"] or "")[:16]
    d["unread"] = s.one("SELECT COUNT(*) c FROM alerts WHERE person_id=? AND seen=0", p["id"])["c"]
    d["entries_total"] = sum(c["rows"] for c in cases)
    d["badges"] = s.badges(p["id"])
    d["custody_history"] = s.q("SELECT * FROM custody_status WHERE person_id=? ORDER BY id DESC LIMIT 10", p["id"])
    return d

@app.get("/monitor")
def monitor():
    return page(T.MONITOR, "monitor", "Monitoring", people=[person_bundle(p) for p in store().people(active_only=False)])

@app.get("/case/<int:cid>")
def case_page(cid):
    s = store(); c = s.one("SELECT * FROM cases WHERE id=?", cid) or abort(404)
    person = s.person(c["person_id"]) or {"last": "?", "first": ""}
    groups = {}
    for e in s.entries_for(cid):
        groups.setdefault(e["kind"], []).append(e)
    return page(T.CASE, "monitor", c["case_number"], c=c, person=person, header=json.loads(c["header_json"] or "{}"), groups=groups)

@app.get("/snapshot")
def snapshot():
    p = pathlib.Path(request.args.get("path", ""))
    if not p.is_file() or base.DATA not in p.parents:
        abort(404)
    return send_file(p, mimetype="text/html" if p.suffix == ".html" else "text/plain", as_attachment=False)

@app.get("/names")
def names():
    return page(T.NAMES, "names", "Names", people=[person_bundle(p) for p in store().people(active_only=False)],
                open_id=request.args.get("open", type=int))

@app.post("/names/edit")
def names_edit():
    f = request.form; pid = int(f["id"]); s = store()
    before = s.person(pid) or abort(404)
    dob = f.get("dob", "").strip()
    if dob and not re.fullmatch(r"\d{2}/\d{2}/\d{4}", dob):
        flash("DOB must be MM/DD/YYYY"); return redirect(f"/names?open={pid}")
    try:
        s.update_person(pid, f["last"], f["first"], f.get("middle", ""), dob, f.get("note", ""), 1 if f.get("is_me") else 0)
    except Exception as e:
        flash(f"Could not save: {e}"); return redirect(f"/names?open={pid}")
    changed = (before["last"].strip().lower(), before["first"].strip().lower(), before["dob"]) != (f["last"].strip().lower(), f["first"].strip().lower(), dob)
    flash(f"Saved {f['last']}, {f['first']}" + (" - name/DOB changed" if changed else ""))
    if f.get("rerun") and changed:
        job = run_job(f"re-search after edit: {f['last']}, {f['first']}", lambda j: full_run(j, "edited_person", [pid], [k for k in watch.SITES if watch.SITES[k][2] == "person"] + ["mcso"]))
        return redirect(f"/job/{job.id}")
    return redirect(f"/names?open={pid}")

@app.post("/names/add")
def names_add():
    f = request.form
    dob = f.get("dob", "").strip()
    if dob and not re.fullmatch(r"\d{2}/\d{2}/\d{4}", dob):
        flash("DOB must be MM/DD/YYYY"); return redirect("/names")
    pid, created = store().add_person(f["last"], f["first"], dob, f.get("middle", ""), f.get("note", ""), 1 if f.get("is_me") else 0)
    flash(("Added " if created else "Already on the list: ") + f"{f['last']}, {f['first']}")
    if f.get("run_now"):
        job = run_job(f"first search for {f['last']}, {f['first']}", lambda j: full_run(j, "new_person", [pid], [k for k in watch.SITES if watch.SITES[k][2] == "person"] + ["mcso"]))
        return redirect(f"/job/{job.id}")
    return redirect("/names")

@app.post("/names/checked")
def names_checked():
    pid = int(request.form["id"]); store().x("UPDATE alerts SET seen=1 WHERE person_id=?", pid)
    return redirect(request.form.get("back") or f"/names?open={pid}")

@app.post("/names/toggle")
def names_toggle():
    store().x("UPDATE people SET active=1-active WHERE id=?", int(request.form["id"])); return redirect("/names")

@app.get("/search")
def search():
    s = store(); q = request.args.get("q", "").strip(); local = request.args.getlist("local") or [k for k, _ in LOCAL_SITES]
    results = None
    if q:
        like = f"%{q}%"; results = {}
        if "cases" in local:
            rows = s.q("SELECT c.id, c.site, c.case_number, c.court, c.party_name, e.kind, e.date, e.text FROM entries e JOIN cases c ON c.id=e.case_id "
                       "WHERE e.text LIKE ? OR c.case_number LIKE ? OR c.party_name LIKE ? ORDER BY e.first_seen DESC LIMIT 300", like, like, like)
            results["Cases & rows"] = [[f'<a href="/case/{r["id"]}">{r["case_number"]}</a>', r["site"], r["court"], r["party_name"], r["kind"], r["date"], r["text"][:300]] for r in rows]
        if "mcso" in local:
            rows = s.q("SELECT * FROM mcso_roster WHERE name LIKE ? OR booking_no LIKE ? OR dob LIKE ? ORDER BY last_seen DESC LIMIT 200", like, like, like)
            results["MCSO roster"] = [[f'<a href="{r["detail_url"]}" target="_blank">{r["booking_no"]}</a>', r["name"], r["dob"], r["status"], r["booking_date"], "gone " + r["gone_at"][:10] if r["gone_at"] else "listed"] for r in rows]
        if "chandler" in local:
            rows = s.q("SELECT * FROM chandler_bookings WHERE arrest_charge LIKE ? OR arrest_address LIKE ? OR related_offense_report_number LIKE ? OR arrest_number LIKE ? OR arrest_date LIKE ? ORDER BY arrest_date DESC LIMIT 200", like, like, like, like, like)
            results["Chandler bookings (no names in dataset)"] = [[r["arrest_number"], r["arrest_date"], r["arrest_charge"], r["arrest_address"], r["related_offense_report_number"]] for r in rows]
        if "powerbi" in local:
            rows = s.q("SELECT id, at, text FROM powerbi_captures WHERE text LIKE ? ORDER BY id DESC LIMIT 5", like)
            out = []
            for r in rows:
                for m in list(re.finditer(re.escape(q), r["text"], re.I))[:10]:
                    out.append([r["at"][:16], "..." + r["text"][max(0, m.start() - 120):m.end() + 120] + "..."])
            results["Power BI captures"] = out
        if "imports" in local:
            rows = s.q("SELECT i.*, c.case_number FROM imports i LEFT JOIN cases c ON c.id=i.person_id WHERE i.filename LIKE ? OR i.note LIKE ? ORDER BY i.id DESC LIMIT 50", like, like)
            results["Imports"] = [[r["at"][:16], r["site"], r["filename"], r["note"]] for r in rows]
    return page(T.SEARCH, "search", "Search", q={"q": q, "local": local, "last": "", "first": "", "dob": ""}, live_sites=LIVE_SITES, local_sites=LOCAL_SITES, results=results)

@app.post("/search/live")
def search_live():
    f = request.form; last, first, dob = f["last"].strip(), f["first"].strip(), f.get("dob", "").strip()
    sites = f.getlist("sites") or [k for k, _ in LIVE_SITES]
    if f.get("save"):
        pid, _ = store().add_person(last, first, dob)
        job = run_job(f"live search + save: {last}, {first}", lambda j: full_run(j, "manual_search", [pid], sites))
        return redirect(f"/job/{job.id}")
    def _fn(job):
        http = Http(); html = []
        if "superior" in sites:
            for court in ("family", "criminal", "civil", "probate"):
                try:
                    hits, _ = superior.search(http, court, last, first)
                    job.log(f"superior/{court}: {len(hits)} hits")
                    html += [f"<tr><td>superior/{court}</td><td><a href='{h.url}' target='_blank'>{h.case_number}</a></td><td>{h.party_name}</td><td>{h.dob}</td></tr>" for h in hits]
                except Exception as e:
                    job.log(f"superior/{court} FAILED {e!r}")
        if "justice" in sites:
            try:
                hits, _ = justice.search(http, last, first, dob); job.log(f"justice: {len(hits)} hits")
                html += [f"<tr><td>justice</td><td><a href='{h.url}' target='_blank'>{h.case_number}</a></td><td>{h.party_name}</td><td>{h.dob}</td></tr>" for h in hits]
            except Exception as e:
                job.log(f"justice FAILED {e!r}")
        if "mesa" in sites:
            try:
                from courtwatch.sites import mesa
                with mesa.Session(last, first, dob) as ses:
                    job.log(f"mesa: {len(ses.hits)} hits")
                    html += [f"<tr><td>mesa</td><td><a href='{h.url}' target='_blank'>{h.case_number}</a></td><td>{h.party_name}</td><td>{h.dob} {h.extra.get('city','')}</td></tr>" for h in ses.hits]
            except Exception as e:
                job.log(f"mesa FAILED {e!r}")
        if "dps" in sites:
            if dob:
                try:
                    st, en, _ = dps.check(http, last, first, dob); job.log(f"dps: {st}")
                    html += [f"<tr><td>dps</td><td>{st}</td><td colspan=2>{' || '.join(e.text for e in en)[:400]}</td></tr>"]
                except Exception as e:
                    job.log(f"dps FAILED {e!r}")
            else:
                job.log("dps skipped: needs DOB")
        job.result_html = "<table><tr><th>Site</th><th>Case</th><th>Party</th><th>DOB shown</th></tr>" + "".join(html) + "</table>" if html else "<div class='mut'>No results on the selected sites.</div>"
    job = run_job(f"live search: {last}, {first} {dob}", _fn)
    return redirect(f"/job/{job.id}")

@app.get("/warrants")
def warrants():
    s = store(); pid = request.args.get("person_id", type=int)
    return page(T.WARRANTS, "warrants", "Warrants", people=s.people(), pid=pid,
                checks=s.q("SELECT * FROM warrant_checks ORDER BY id DESC LIMIT 100"))

@app.post("/warrants/check")
def warrants_check():
    s = store(); f = request.form
    if f.get("last") and f.get("first"):
        if not f.get("dob"):
            flash("DPS needs a DOB"); return redirect("/warrants")
        pid, _ = s.add_person(f["last"], f["first"], f["dob"])
    else:
        pid = int(f["person_id"])
    run_id = s.start_run("manual_warrant")
    def _fn(job):
        p = s.person(pid); watch.run_dps(s, Http(), p, run_id, job.log); s.finish_run(run_id, {"manual_warrant": pid}); job.log("done")
    job = run_job("warrant check", _fn)
    return redirect(f"/job/{job.id}")

@app.get("/arrests")
def arrests():
    s = store(); mq, cq, pq = request.args.get("mq", "").strip(), request.args.get("cq", "").strip(), request.args.get("pq", "").strip()
    mrows = s.q("SELECT * FROM mcso_roster WHERE name LIKE ? OR booking_no LIKE ? OR dob LIKE ? ORDER BY last_seen DESC LIMIT 200", f"%{mq}%", f"%{mq}%", f"%{mq}%") if mq else None
    crows = None
    if cq:
        like = f"%{cq}%"
        crows = [dict(r, age=json.loads(r["data_json"]).get("arrestee_age", "")) for r in s.q("SELECT * FROM chandler_bookings WHERE arrest_charge LIKE ? OR arrest_address LIKE ? OR related_offense_report_number LIKE ? OR arrest_number LIKE ? OR arrest_date LIKE ? ORDER BY arrest_date DESC LIMIT 200", like, like, like, like, like)]
    prows = None
    if pq:
        prows = []
        for r in s.q("SELECT at, text FROM powerbi_captures WHERE text LIKE ? ORDER BY id DESC LIMIT 3", f"%{pq}%"):
            for m in list(re.finditer(re.escape(pq), r["text"], re.I))[:8]:
                prows.append(f"[{r['at'][:16]}] ..." + r["text"][max(0, m.start() - 160):m.end() + 160] + "...")
    matches = s.q("SELECT m.person_id, r.* FROM mcso_matches m JOIN mcso_roster r ON r.booking_no=m.booking_no ORDER BY m.id DESC")
    return page(T.ARRESTS, "arrests", "Arrests", mcso=s.one("SELECT * FROM mcso_fetches ORDER BY id DESC LIMIT 1"), ch=s.one("SELECT * FROM chandler_fetches ORDER BY id DESC LIMIT 1"),
                roster_total=s.one("SELECT COUNT(*) c FROM mcso_roster")["c"], ch_total=s.one("SELECT COUNT(*) c FROM chandler_bookings")["c"],
                matches=matches, mq=mq, mrows=mrows, cq=cq, crows=crows, pq=pq, prows=prows,
                pbi=s.q("SELECT id, at, pages, responses, dir FROM powerbi_captures ORDER BY id DESC LIMIT 20"))

@app.get("/payments")
def payments():
    s = store(); out = []
    for c in s.q("SELECT DISTINCT c.* FROM cases c JOIN entries e ON e.case_id=c.id WHERE e.kind IN ('balance','payment') ORDER BY c.site, c.case_number"):
        rows = s.q("SELECT * FROM entries WHERE case_id=? AND kind IN ('balance','payment','event') ORDER BY kind, date DESC", c["id"])
        rem = []
        for e in rows:
            f = json.loads(e["fields_json"] or "{}")
            if e["kind"] == "balance" and f.get("Due Date"):
                iso = base.mdy_to_iso(f["Due Date"])
                if iso:
                    rem.append({"label": f"{f.get('Amount Due','')} due {f['Due Date']}", "ics": f"/ics?case={c['case_number']}&date={iso}&title={urllib.parse.quote('Court payment ' + f.get('Amount Due','') + ' ' + c['case_number'])}"})
        out.append(dict(c, rows=rows, reminders=rem))
    return page(T.PAYMENTS, "payments", "Payments", cases=out)

@app.get("/ics")
def ics():
    d = request.args.get("date", ""); title = request.args.get("title", "Court payment"); case = request.args.get("case", "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        abort(400)
    due = datetime.date.fromisoformat(d); remind = due - datetime.timedelta(days=3)
    body = "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//EDISON Court Watch//EN",
                        "BEGIN:VEVENT", f"UID:{case}-{d}@edison-court-watch", f"DTSTAMP:{datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
                        f"DTSTART;VALUE=DATE:{due.strftime('%Y%m%d')}", f"SUMMARY:{title}", f"DESCRIPTION:Due {d} - case {case}. Pay at the court's site.",
                        "BEGIN:VALARM", "ACTION:DISPLAY", f"TRIGGER;VALUE=DATE-TIME:{remind.strftime('%Y%m%d')}T160000Z", "DESCRIPTION:Court payment due in 3 days", "END:VALARM",
                        "END:VEVENT", "END:VCALENDAR", ""])
    r = make_response(body); r.headers["Content-Type"] = "text/calendar"; r.headers["Content-Disposition"] = f"attachment; filename=payment-{case}-{d}.ics"; return r

@app.get("/import")
def import_page():
    s = store()
    return page(T.IMPORT, "import", "Import", people=s.people(), imports=s.q("SELECT * FROM imports ORDER BY id DESC LIMIT 50"))

@app.post("/import")
def import_post():
    s = store(); f = request.files["file"]; body = f.read()
    site, pid, cn = request.form.get("site", "other"), int(request.form["person_id"]), request.form["case_number"].strip()
    snap = base.save_snapshot(site, f"import:{cn}", f"file://{f.filename}", body, "text/html")
    s.snapshot(snap)
    title, entries = azcourts.parse_saved(body)
    from courtwatch.sites import mcso as _mcso
    url = {"azcourts": azcourts.URL, "maricopa_inmate": _mcso.MARICOPA_INMATE_URL}.get(site, "")
    cid, _ = s.upsert_case(pid, site, cn, court=request.form.get("court", ""), url=url, title=title)
    new = s.add_entries(cid, entries, snap, snap.url)
    s.x("INSERT INTO imports(at, site, person_id, filename, sha256, path, note, rows) VALUES (?,?,?,?,?,?,?,?)", now(), site, pid, f.filename, snap.sha256, snap.path, request.form.get("note", ""), len(entries))
    msg = f"Imported {len(entries)} table rows ({len(new)} new) from {f.filename} into case {cn}"
    if site == "maricopa_inmate":
        txt = base.norm_ws(base.soup(body).get_text(" ", strip=True))
        not_found = re.search(r"not currently in custody|cannot be found", txt, re.I)
        bonds = re.findall(r"Bond(?:\s*Amount)?\s*:?\s*\$\s?([\d,]+(?:\.\d{2})?)", txt, re.I)
        bk = re.search(r"Booking\s*(?:Number|#)\s*:?\s*([A-Z0-9-]{6,})", txt, re.I)
        in_custody = not not_found and bool(bonds or bk or re.search(r"in custody|custody status", txt, re.I))
        bond_raw = ("$" + " / $".join(bonds)) if bonds else ("MISSING_DATA" if in_custody else "")
        s.custody(pid, "maricopa_inmate", in_custody, bond_raw=bond_raw, booking_no=bk.group(1) if bk else "",
                  details=("IMPORTED PAGE: " + txt[:600]), snapshot_path=snap.path, url=url)
        if in_custody:
            s.alert(pid, "maricopa_inmate", "arrest_match", f"Maricopa inmate page imported: in custody; bond {bond_raw}; booking {bk.group(1) if bk else 'MISSING_DATA'}", url=url)
        msg += f" - custody: {'IN CUSTODY, bond ' + bond_raw if in_custody else 'not in custody / not found'}"
    flash(msg)
    return redirect(f"/case/{cid}")

def seed_me():
    """WATCH_ME="Last,First,MM/DD/YYYY[,Middle]" in .env creates the is_me person once."""
    v = os.environ.get("WATCH_ME", "")
    if not v or store().one("SELECT id FROM people WHERE is_me=1"):
        return
    parts = [x.strip() for x in v.split(",")] + ["", "", ""]
    pid, created = store().add_person(parts[0], parts[1], parts[2], parts[3], "from WATCH_ME", is_me=1)
    store().x("UPDATE people SET is_me=1 WHERE id=?", pid)
    print(f"[seed] is_me person {'created' if created else 'exists'}: {parts[0]}, {parts[1]}", flush=True)

if __name__ == "__main__":
    seed_me()
    threading.Thread(target=scheduler, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8090")), threaded=True)
