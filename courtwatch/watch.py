"""
The 3x/day run. For every active person on the names list, query every enabled site, store what came back
(snapshots + parsed rows), diff against what was already stored, raise alerts, and export "me" to EDISON.
"""
import json, os, re, traceback, datetime
from .db import Store, now
from .sites import base, superior, justice, dps, mcso, chandler, powerbi, azcourts
from .sites.base import Http
from . import edison_export

SITES = {   # key -> (label, needs_dob, kind)
    "superior": ("Maricopa Superior Court docket (Family/Criminal/Civil/Probate)", False, "person"),
    "justice":  ("Maricopa Justice Courts", False, "person"),
    "mesa":     ("Mesa Municipal Court eCourt", False, "person"),
    "dps":      ("AZ DPS warrant search", True, "person"),
    "azcourts": ("AZ Courts public access (manual - image CAPTCHA)", False, "person"),
    "mcso":     ("Jail roster - portal.mobileso.com (Mobile County Sheriff, Alabama)", False, "bulk"),
    "chandler": ("Chandler PD arrest bookings CSV", False, "bulk"),
    "powerbi":  ("Power BI Gov report capture", False, "bulk"),
}
DEFAULT_SITES = [k for k in SITES]
POWERBI_URL = os.environ.get("WATCH_POWERBI_URL", "")

def dob_month_year_matches(person_dob: str, shown: str) -> bool:
    """Superior/Justice/Mesa show 'M/YYYY' or 'MM/YYYY'. Compare month+year with the person's DOB (MM/DD/YYYY)."""
    m1 = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", person_dob or "")
    m2 = re.match(r"^(\d{1,2})/(\d{4})$", (shown or "").strip())
    if not (m1 and m2):
        return False
    return int(m1.group(1)) == int(m2.group(1)) and m1.group(3) == m2.group(2)

def _ingest_case(store, person, hit, casedata, run_id, first_run_for_person, log):
    cid, is_new_case = store.upsert_case(person["id"], hit.site, hit.case_number, court=casedata.court or hit.court, url=hit.url,
                                         party_name=hit.party_name, dob_shown=hit.dob, header=casedata.header,
                                         title=(casedata.header.get("Case Type") or ""))
    store.snapshot(casedata.snapshot)
    new = store.add_entries(cid, casedata.entries, casedata.snapshot, casedata.url)
    store.set_case_fetch(cid, "ok")
    if is_new_case:
        store.alert(person["id"], hit.site, "baseline_case" if first_run_for_person else "new_case",
                    f"{'Existing' if first_run_for_person else 'NEW'} case {hit.case_number} ({casedata.court or hit.court}) - {hit.party_name} - {len(casedata.entries)} rows captured",
                    hit.case_number, hit.url)
    elif new:
        for n in new:
            store.alert(person["id"], hit.site, "new_entry", f"{hit.case_number}: [{n['kind']}] {n['text']}", hit.case_number, hit.url)
    log(f"  {hit.site} {hit.case_number}: rows={len(casedata.entries)} new={len(new)}{' NEW CASE' if is_new_case else ''}")
    return cid, is_new_case, len(new)

def run_superior(store, http, person, run_id, first_run, log):
    total_found = total_new = 0
    for court in ("family", "criminal", "civil", "probate"):
        try:
            hits, snap = superior.search(http, court, person["last"], person["first"])
            store.snapshot(snap)
        except Exception as e:
            store.result(run_id, person["id"], "superior", "failed", message=f"{court} search: {e!r}")
            log(f"  superior/{court} search FAILED: {e!r}"); continue
        mine = []
        for h in hits:
            if person["dob"]:
                if h.dob and dob_month_year_matches(person["dob"], h.dob):
                    mine.append(h)
            else:
                mine.append(h)     # no DOB on file: every name match is a candidate (flagged in the UI)
        seen = set()
        for h in mine:
            if h.case_number in seen:
                continue
            seen.add(h.case_number)
            try:
                cd = superior.case(http, h.case_number, h.extra.get("court_key"))
                _, isnew, n = _ingest_case(store, person, h, cd, run_id, first_run, log)
                total_found += 1; total_new += n + (1 if isnew else 0)
            except Exception as e:
                log(f"  superior case {h.case_number} FAILED: {e!r}")
                store.result(run_id, person["id"], "superior", "failed", message=f"case {h.case_number}: {e!r}")
        store.result(run_id, person["id"], "superior", "found" if mine else "none", found=len(mine), new_items=total_new,
                     message=f"{court}: {len(hits)} name hits, {len(mine)} matching DOB" + ("" if person["dob"] else " (no DOB on file - unverified)"),
                     detail={"court": court, "hits": [h.as_dict() for h in hits]})
    return total_found, total_new

def run_justice(store, http, person, run_id, first_run, log):
    try:
        hits, snap = justice.search(http, person["last"], person["first"], person["dob"] or "")
        store.snapshot(snap)
    except Exception as e:
        store.result(run_id, person["id"], "justice", "failed", message=f"search: {e!r}"); return 0, 0
    found = new_total = 0
    for h in hits:
        if person["dob"] and h.extra.get("dob_full") and h.extra["dob_full"].lstrip("0").replace("/0", "/") != person["dob"].lstrip("0").replace("/0", "/"):
            continue
        try:
            cd = justice.case(http, h.case_number)
            _, isnew, n = _ingest_case(store, person, h, cd, run_id, first_run, log)
            found += 1; new_total += n + (1 if isnew else 0)
        except Exception as e:
            store.result(run_id, person["id"], "justice", "failed", message=f"case {h.case_number}: {e!r}")
    store.result(run_id, person["id"], "justice", "found" if found else "none", found=found, new_items=new_total,
                 message=f"{len(hits)} hits", detail={"hits": [h.as_dict() for h in hits]})
    return found, new_total

def run_dps(store, http, person, run_id, log):
    if not person["dob"]:
        store.result(run_id, person["id"], "dps", "skipped", message="DPS requires a date of birth"); return
    try:
        status, entries, snap = dps.check(http, person["last"], person["first"], person["dob"])
        store.snapshot(snap)
    except Exception as e:
        store.result(run_id, person["id"], "dps", "failed", message=repr(e)); return
    text = " || ".join(e.text for e in entries)
    store.x("INSERT INTO warrant_checks(person_id, checked_at, status, text, snapshot_sha, snapshot_path, url) VALUES (?,?,?,?,?,?,?)",
            person["id"], now(), status, text, snap.sha256, snap.path, snap.url)
    prev = store.q("SELECT status FROM warrant_checks WHERE person_id=? ORDER BY id DESC LIMIT 2", person["id"])
    if status == "found":
        store.alert(person["id"], "dps", "warrant", f"DPS warrant search returned results: {text[:1500]}", url=snap.url)
    elif status == "unknown_layout":
        store.alert(person["id"], "dps", "site_changed", "DPS page layout not recognised - check manually", url=snap.url)
    store.result(run_id, person["id"], "dps", status if status != "unknown_layout" else "failed",
                 found=1 if status == "found" else 0, message=text[:300])

def run_mesa(store, person, run_id, first_run, log):
    from .sites import mesa
    try:
        with mesa.Session(person["last"], person["first"], person["dob"] or "") as ses:
            hits = ses.hits
            store.snapshot(ses.search_snapshot)
            found = new_total = 0
            for h in hits:
                if person["dob"] and h.dob and not dob_month_year_matches(person["dob"], h.dob):
                    continue
                try:
                    cd = ses.case(h.case_number)
                    _, isnew, n = _ingest_case(store, person, h, cd, run_id, first_run, log)
                    found += 1; new_total += n + (1 if isnew else 0)
                except Exception as e:
                    log(f"  mesa case {h.case_number} FAILED: {e!r}\n{traceback.format_exc()[-600:]}")
                    store.result(run_id, person["id"], "mesa", "failed", message=f"case {h.case_number}: {e!r}")
            store.result(run_id, person["id"], "mesa", "found" if found else "none", found=found, new_items=new_total,
                         message=f"{len(hits)} hits", detail={"hits": [h.as_dict() for h in hits]})
    except Exception as e:
        log(f"  mesa FAILED: {e!r}")
        store.result(run_id, person["id"], "mesa", "failed", message=repr(e)[:500])

def run_mcso(store, http, people, run_id, log):
    try:
        r = mcso.fetch(http)
    except Exception as e:
        store.result(run_id, 0, "mcso", "failed", message=repr(e)); return
    t = now(); new_rows = 0
    seen = set()
    for row in r["rows"]:
        seen.add(row["booking_no"])
        ex = store.one("SELECT booking_no FROM mcso_roster WHERE booking_no=?", row["booking_no"])
        if ex:
            store.x("UPDATE mcso_roster SET status=?, expected_release=?, arrival_date=?, last_seen=?, gone_at='' WHERE booking_no=?",
                    row["status"], row["expected_release"], row["arrival_date"], t, row["booking_no"])
        else:
            new_rows += 1
            store.x("INSERT INTO mcso_roster(booking_no,name,race,sex,dob,status,age,booking_date,arrival_date,expected_release,detail_url,first_seen,last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    row["booking_no"], row["name"], row["race"], row["sex"], row["dob"], row["status"], row["age"], row["booking_date"],
                    row["arrival_date"], row["expected_release"], row["detail_url"], t, t)
    store.x("UPDATE mcso_roster SET gone_at=? WHERE gone_at='' AND last_seen<>?", t, t)
    store.x("INSERT INTO mcso_fetches(at,title,rows,new_rows,snapshot_path,sha256) VALUES (?,?,?,?,?,?)", t, r["title"], len(r["rows"]), new_rows, r["snapshot"].path, r["snapshot"].sha256)
    store.snapshot(r["snapshot"])
    log(f"  mcso: {r['title']} rows={len(r['rows'])} new={new_rows}")
    for p in people:
        matches = [row for row in r["rows"] if mcso.name_matches(row, p["last"], p["first"], p["dob"] or "")]
        for row in matches:
            try:
                store.x("INSERT INTO mcso_matches(person_id, booking_no, first_seen) VALUES (?,?,?)", p["id"], row["booking_no"], t)
                store.alert(p["id"], "mcso", "arrest_match", f"{mcso.ROSTER_LABEL} lists {row['name']} DOB {row['dob']} booking {row['booking_no']} booked {row['booking_date']} status {row['status']}", url=row["detail_url"])
            except Exception:
                pass
        # custody badge: listed on this pull => in custody per this roster (bond is not shown on the roster page)
        last = store.one("SELECT in_custody FROM custody_status WHERE person_id=? AND source=? ORDER BY id DESC LIMIT 1", p["id"], mcso.SITE)
        if matches:
            m = matches[0]
            if not last or not last["in_custody"]:
                store.custody(p["id"], mcso.SITE, True, bond_raw="MISSING_DATA (roster shows no bond)", booking_no=m["booking_no"],
                              details=f"{m['name']} status {m['status']} booked {m['booking_date']} expected release {m['expected_release'] or 'MISSING_DATA'}",
                              snapshot_path=r["snapshot"].path, url=m["detail_url"])
        elif last and last["in_custody"]:
            store.custody(p["id"], mcso.SITE, False, details="no longer listed on the roster", snapshot_path=r["snapshot"].path)
        store.result(run_id, p["id"], "mcso", "found" if matches else "none", found=len(matches),
                     message=f"roster {r['title']}: {len(matches)} match(es)" + ("" if p["dob"] else " (name only - no DOB on file)"))

def run_chandler(store, http, run_id, log):
    try:
        r = chandler.fetch(http)
    except Exception as e:
        store.result(run_id, 0, "chandler", "failed", message=repr(e)); return
    t = now(); new_rows = 0
    with store.con:
        for row in r["rows"]:
            rid = row.get("id") or row.get("arrest_id")
            if not rid:
                continue
            cur = store.con.execute("INSERT OR IGNORE INTO chandler_bookings(id,arrest_number,arrest_date,arrest_time,arrest_charge,arrest_address,arrest_city,related_offense_report_number,arrestee_unique_number,data_json,first_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                    (rid, row.get("arrest_number", ""), row.get("arrest_date", ""), row.get("arrest_time", ""), row.get("arrest_charge", ""),
                                     row.get("arrest_address", ""), row.get("arrest_city", ""), row.get("related_offense_report_number", ""),
                                     row.get("arrestee_unique_number", ""), json.dumps(row, ensure_ascii=False), t))
            new_rows += cur.rowcount
    store.x("INSERT INTO chandler_fetches(at,rows,new_rows,snapshot_path,sha256) VALUES (?,?,?,?,?)", t, len(r["rows"]), new_rows, r["snapshot"].path, r["snapshot"].sha256)
    store.snapshot(r["snapshot"])
    store.result(run_id, 0, "chandler", "found", found=len(r["rows"]), new_items=new_rows, message=f"{len(r['rows'])} rows, {new_rows} new (dataset has no names)")
    log(f"  chandler: rows={len(r['rows'])} new={new_rows}")

def run_powerbi(store, run_id, log):
    if not POWERBI_URL:
        store.result(run_id, 0, "powerbi", "skipped", message="WATCH_POWERBI_URL not set"); return
    try:
        meta, texts, jtext = powerbi.capture(POWERBI_URL)
    except Exception as e:
        store.result(run_id, 0, "powerbi", "failed", message=repr(e)[:500]); log(f"  powerbi FAILED {e!r}"); return
    alltext = " \n".join(f"[page {t['page']}] {t['text']}" for t in texts) + " \n[data] " + jtext
    store.x("INSERT INTO powerbi_captures(at,url,dir,pages,responses,text,meta_json) VALUES (?,?,?,?,?,?,?)",
            now(), POWERBI_URL, meta["dir"], meta["pages"], len(meta["responses"]), alltext[:2_000_000], json.dumps(meta))
    store.result(run_id, 0, "powerbi", "found", found=meta["pages"], message=f"{meta['pages']} page(s), {len(meta['responses'])} data responses captured")
    log(f"  powerbi: pages={meta['pages']} responses={len(meta['responses'])}")

def run(trigger="scheduled", people_ids=None, sites=None, log=print):
    store = Store()
    sites = sites or [s for s in DEFAULT_SITES if s not in os.environ.get("WATCH_DISABLED_SITES", "").split(",")]
    run_id = store.start_run(trigger)
    people = [p for p in store.people() if not people_ids or p["id"] in people_ids]
    log(f"[watch] run {run_id} trigger={trigger} people={[p['last']+','+p['first'] for p in people]} sites={sites}")
    http = Http()
    summary = {}
    for p in people:
        first_run = not store.one("SELECT 1 FROM run_results WHERE person_id=? AND status IN ('found','none')", p["id"])
        log(f"[watch] person {p['last']}, {p['first']} dob={'yes' if p['dob'] else 'no'} first_run={first_run}")
        if "superior" in sites:
            run_superior(store, http, p, run_id, first_run, log)
        if "justice" in sites:
            run_justice(store, http, p, run_id, first_run, log)
        if "mesa" in sites:
            run_mesa(store, p, run_id, first_run, log)
        if "dps" in sites:
            run_dps(store, http, p, run_id, log)
        if "azcourts" in sites:
            store.result(run_id, p["id"], "azcourts", "manual_required", message=azcourts.MANUAL_REASON)
    if "mcso" in sites:
        run_mcso(store, http, people, run_id, log)
    if "chandler" in sites:
        run_chandler(store, http, run_id, log)
    if "powerbi" in sites:
        run_powerbi(store, run_id, log)
    if os.environ.get("EDISON_EXPORT", "1") == "1":
        try:
            w, rj, msg = edison_export.export_me(store, log)
            summary["edison_export"] = {"written": w, "rejected": rj, "msg": msg}
        except Exception as e:
            summary["edison_export"] = {"error": repr(e)}; log(f"[watch] export error {e!r}")
    else:
        summary["edison_export"] = "disabled (EDISON_EXPORT=0)"
    rows = store.q("SELECT site, status, COUNT(*) c FROM run_results WHERE run_id=? GROUP BY site, status", run_id)
    summary["results"] = [dict(r) for r in rows]
    store.finish_run(run_id, summary)
    log(f"[watch] run {run_id} done: {summary}")
    return run_id, summary
