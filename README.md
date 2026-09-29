# azlawsearch

Self-hosted watcher for Arizona public court / law-enforcement records. Keeps a names list, checks every
source three times a day, stores every page it fetched byte-for-byte (with SHA-256) plus the rows parsed
from it verbatim, and raises alerts when something new appears. Web UI with Dashboard, Monitoring,
Search, Payments, Warrants, Arrests, Names and Import tabs.

## Sources

| Source | Method | Notes |
|---|---|---|
| Maricopa County Superior Court docket (Family / Criminal / Civil / Probate) | direct fetch | name search + every matching case page (docket, calendar, parties, dispositions) |
| Maricopa County Justice Courts | direct fetch | name + DOB search, case pages (parties, dispositions, documents, events) |
| Mesa Municipal Court eCourt | headless Chromium | name search, then Balance/Pay, Events and Disposition pages per case; payment due dates feed the Payments tab and `.ics` reminders |
| AZ DPS warrant search | direct fetch | needs a DOB; "found" lights the warrant badge |
| Chandler PD arrest bookings CSV | direct fetch | open data; no names in the dataset |
| Power BI (gov) published report | headless Chromium | screenshots + raw data responses, text-searchable |
| AZ Courts public access (`apps.azcourts.gov`) | manual import | image CAPTCHA, so: search in your browser, save the page, upload it on Import |
| Maricopa County inmate lookup (`mcso.org/InmateInfo`) | manual import | reCAPTCHA checkbox, so: search there, save the page, import it; custody + bond badge come from the saved page |

Nothing is inferred. If a page does not show a value, the row says `MISSING_DATA`.

## Run

```
cp .env.example .env   # set CW_PIN
docker compose up -d --build
# open http://<host>:8095
```

Add people on the **Names** tab (DOB optional but needed for DPS / Justice Courts). Adding a name runs
all sites immediately; after that every name is re-checked at `WATCH_TIMES`.

Badges: 🚨 WARRANT (latest DPS check found results), 🔒 IN CUSTODY · bond (roster match or imported
inmate page), 🆕 N new (unread new rows/cases since "mark checked").

## EDISON integration

Built as the `court-watch` service of [mylife-edison-stack](https://github.com/xmindpingx/mylife-edison-stack).
With `EDISON_EXPORT=1` and that stack reachable, rows for the person flagged "me" are exported as
schema-validated EDISON records (`courtwatch/edison_export.py`) into edison.db + Qdrant via `load_db.py`.
Everyone else stays in `data/court_watch/court_watch.db`.

## Layout

```
courtwatch/app.py         Flask UI + scheduler
courtwatch/watch.py       the run: per-person sites, bulk sources, diff, alerts, export
courtwatch/db.py          SQLite (court_watch.db)
courtwatch/sites/*.py     one adapter per source, each documenting the page layout it was verified against
schema.json               EDISON record schema (used only for the optional export)
```
