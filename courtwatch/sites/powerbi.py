"""
Power BI (GCC) published report  (app.powerbigov.us/view?r=...)

A Power BI report is rendered entirely in the browser; there is no HTML table to read. What can be
captured faithfully is (a) the report's own data responses (the JSON the visuals load, saved verbatim),
(b) a full-page screenshot of every report page, and (c) the visible text of each page. All three are
stored under /data/court_watch/powerbi/<timestamp>/ and the visible text + JSON text are indexed for
the local search. This is a capture, not a parser: nothing is interpreted.
"""
import json, re, time, pathlib, datetime
from .base import DATA, now_iso, sha256_bytes, norm_ws
from . import browser

SITE = "powerbi"

def capture(url: str, max_pages: int = 20):
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = DATA / "powerbi" / ts; out.mkdir(parents=True, exist_ok=True)
    responses, notes = [], []
    with browser.page() as pg:
        def on_response(resp):
            try:
                u = resp.url
                if re.search(r"querydata|/query|conceptualschema|exploration|report", u, re.I) and "json" in (resp.headers.get("content-type") or ""):
                    body = resp.body()
                    n = len(responses) + 1
                    f = out / f"response_{n:03d}.json"; f.write_bytes(body)
                    responses.append({"n": n, "url": u, "bytes": len(body), "sha256": sha256_bytes(body), "path": str(f)})
            except Exception as e:      # a response body that is gone by the time we read it
                notes.append(f"response skipped: {e!r}")
        pg.on("response", on_response)
        pg.goto(url, wait_until="networkidle", timeout=120000)
        time.sleep(8)
        texts, shots = [], []
        # report pages appear as tabs in the bottom bar; click through them
        tabs = pg.query_selector_all("[role=tab], .pageNavigationTab, button.pbi-page-tab, .page-navigation-item")
        n_pages = max(1, min(len(tabs), max_pages))
        for i in range(n_pages):
            if tabs and i < len(tabs):
                try:
                    tabs[i].click(); pg.wait_for_load_state("networkidle", timeout=60000); time.sleep(4)
                except Exception as e:
                    notes.append(f"tab {i} click failed: {e!r}")
            shot = out / f"page_{i+1:02d}.png"
            try:
                pg.screenshot(path=str(shot), full_page=True); shots.append(str(shot))
            except Exception as e:
                notes.append(f"screenshot {i} failed: {e!r}")
            try:
                t = norm_ws(pg.evaluate("() => document.body.innerText"))
            except Exception:
                t = ""
            texts.append({"page": i + 1, "text": t})
            (out / f"page_{i+1:02d}.txt").write_text(t)
    # a flat text of every JSON response for search
    json_text = []
    for r in responses:
        try:
            raw = pathlib.Path(r["path"]).read_text(errors="replace")
            json_text.append(norm_ws(re.sub(r"[\[\]{}\"]", " ", raw))[:200000])
        except Exception:
            pass
    meta = {"url": url, "captured_at": now_iso(), "dir": str(out), "responses": responses, "screenshots": shots,
            "pages": len(texts), "notes": notes}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    return meta, texts, " ".join(json_text)
