# HTML for the court-watch UI. Same dark theme as the upload page so it feels like one app.
BASE = r"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }} - EDISON Court Watch</title>
<style>
:root{--bg:#0f1115;--card:#181b22;--fg:#e8eaf0;--mut:#8a90a0;--acc:#4f8cff;--ok:#2ecc71;--bad:#ff5c5c;--warn:#f5b342;--line:#262a33}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 -apple-system,system-ui,sans-serif;padding:0 14px 40px}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
nav{display:flex;flex-wrap:wrap;gap:6px;padding:12px 0;border-bottom:1px solid var(--line);margin-bottom:14px;position:sticky;top:0;background:var(--bg);z-index:5}
nav a{padding:7px 11px;border-radius:9px;background:#1d212a;color:var(--fg);font-size:14px}nav a.on{background:var(--acc);color:#fff}
nav a .n{background:var(--bad);color:#fff;border-radius:9px;padding:0 6px;font-size:12px;margin-left:4px}
h1{font-size:20px;margin:6px 0 10px}h2{font-size:16px;margin:16px 0 8px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}
.card{background:var(--card);border-radius:12px;padding:12px 14px;margin-bottom:12px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:6px 0}
input[type=text],input[type=password],input[type=date],select{padding:9px 10px;border-radius:9px;border:1px solid var(--line);background:#0f1115;color:var(--fg);font-size:15px}
button,.btn{padding:9px 14px;border-radius:9px;border:0;background:var(--acc);color:#fff;font-weight:600;font-size:14px;cursor:pointer}
button.alt,.btn.alt{background:#2a2f3a}button.danger{background:var(--bad)}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:6px 8px;border-top:1px solid var(--line);vertical-align:top}th{color:var(--mut);font-weight:600;border-top:0}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}.mut{color:var(--mut)}.small{font-size:13px}
.pill{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;background:#2a2f3a}.pill.found{background:#1f5f3a}.pill.none{background:#2a2f3a}.pill.failed{background:#6b2323}.pill.skipped{background:#5a4a1a}.pill.manual_required{background:#4a3a6b}
details>summary{cursor:pointer;padding:8px 0;font-weight:600}details{border-top:1px solid var(--line)}
pre{white-space:pre-wrap;word-break:break-word;background:#0f1115;padding:10px;border-radius:9px;font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px}
label.chk{display:inline-flex;gap:6px;align-items:center;padding:6px 10px;border:1px solid var(--line);border-radius:9px;font-size:14px}
.badge{display:inline-block;padding:2px 9px;border-radius:10px;font-size:12px;font-weight:700;margin-left:4px;vertical-align:middle}.badge.warrant{background:#b3261e;color:#fff}.badge.custody{background:#6b2d8f;color:#fff}.badge.newinfo{background:#1f5f3a;color:#fff}
.alert{border-left:4px solid var(--warn);padding:6px 10px;margin:6px 0;background:#1d212a;border-radius:6px}.alert.new_case,.alert.warrant,.alert.arrest_match{border-color:var(--bad)}.alert.baseline_case{border-color:var(--mut)}
</style></head><body>
<nav>
{% for key,label,badge in tabs %}<a href="/{{ key }}" class="{{ 'on' if key==tab else '' }}">{{ label }}{% if badge %}<span class="n">{{ badge }}</span>{% endif %}</a>{% endfor %}
</nav>
{% with msgs = get_flashed_messages() %}{% for m in msgs %}<div class="card warn">{{ m }}</div>{% endfor %}{% endwith %}
{% macro badges(b) -%}
{% if b.warrant %}<span class="badge warrant" title="latest DPS warrant search returned results">🚨 WARRANT</span>{% endif %}
{% for c in b.custody %}<span class="badge custody" title="{{ c.source }} · {{ c.seen_at[:16] }}">🔒 IN CUSTODY{% if c.bond_raw and c.bond_raw != 'MISSING_DATA (roster shows no bond)' %} · bond {{ c.bond_raw }}{% else %} · bond MISSING_DATA{% endif %}</span>{% endfor %}
{% if b.new_info %}<span class="badge newinfo" title="unread new information since last checked">🆕 {{ b.new_info }} new</span>{% endif %}
{%- endmacro %}
{% block body %}{% endblock %}
</body></html>"""

DASH = r"""{% extends "base" %}{% block body %}
<h1>Court Watch</h1>
<div class="card">
 <div class="row"><b>Schedule:</b> <span>{{ times }} ({{ tz }})</span> <span class="mut">next run {{ next_run }}</span>
 <form method="post" action="/run" style="margin-left:auto"><button>Run everything now</button></form></div>
 <div class="row small mut">Last run: {{ last_run.started_at if last_run else 'never' }} {% if last_run %}({{ last_run.trigger_ }}) {{ 'finished ' + last_run.finished_at if last_run.finished_at else 'RUNNING' }}{% endif %}
 {% if job %}&nbsp;|&nbsp; job {{ job.id }}: {{ job.status }} - <a href="/job/{{ job.id }}">log</a>{% endif %}</div>
</div>
<h2>People</h2>
<div class="card">{% for p in people %}<div class="row"><a href="/names?open={{ p.id }}"><b>{{ p.last }}, {{ p.first }}</b></a> {{ badges(p.badges) }}
{% if not p.badges.warrant and not p.badges.custody and not p.badges.new_info %}<span class="mut small">no warrant on DPS · not on any roster · nothing new</span>{% endif %}</div>{% endfor %}</div>
<h2>Alerts ({{ alerts|length }} unread)</h2>
<div class="card">
{% if not alerts %}<div class="mut">No unread alerts.</div>{% endif %}
{% for a in alerts %}<div class="alert {{ a.kind }}"><span class="mut small">{{ a.created_at[:16] }} · {{ a.site }} · {{ a.kind }} · {{ people_names.get(a.person_id,'') }}</span><br>{{ a.text }} {% if a.url %}<a href="{{ a.url }}" target="_blank">open</a>{% endif %}</div>{% endfor %}
{% if alerts %}<form method="post" action="/alerts/seen"><button class="alt">Mark all read</button></form>{% endif %}
</div>
<h2>Latest run by site</h2>
<div class="card"><table><tr><th>Person</th><th>Site</th><th>Status</th><th>Found</th><th>New</th><th>Message</th><th>When</th></tr>
{% for r in latest %}<tr><td>{{ people_names.get(r.person_id,'(bulk)') }}</td><td>{{ r.site }}</td><td><span class="pill {{ r.status }}">{{ r.status }}</span></td><td>{{ r.found }}</td><td>{{ r.new_items }}</td><td class="small">{{ r.message }}</td><td class="small mut">{{ r.at[:16] }}</td></tr>{% endfor %}
</table></div>
{% endblock %}"""

MONITOR = r"""{% extends "base" %}{% block body %}
<h1>Monitoring</h1>
<p class="mut small">Everyone on the names list. Expand a person for the per-site breakdown of the latest run and every case found. Only <b>you</b> (is_me) are exported into the EDISON "my life" database; everyone else stays in this Monitoring database.</p>
{% for p in people %}
<div class="card">
<details {% if people|length==1 %}open{% endif %}><summary>{{ p.last }}, {{ p.first }} {{ p.middle }} {% if p.dob %}<span class="mut">DOB {{ p.dob }}</span>{% else %}<span class="warn">no DOB - name-only matches, unverified</span>{% endif %}
 {% if p.is_me %}<span class="pill found">me</span>{% endif %} {{ badges(p.badges) }} &nbsp; <span class="mut small">last legal update: {{ p.last_update or 'none' }} · cases: {{ p.cases|length }} · unread alerts: {{ p.unread }}</span></summary>
 <div class="row"><form method="post" action="/run"><input type="hidden" name="person_id" value="{{ p.id }}"><button class="alt">Run now for this person</button></form>
 <a class="btn alt" href="/warrants?person_id={{ p.id }}">warrant checks</a></div>
 <h2>Latest run - per site</h2>
 <table><tr><th>Site</th><th>Status</th><th>Found</th><th>New</th><th>Message</th><th>When</th></tr>
 {% for r in p.latest %}<tr><td>{{ site_labels.get(r.site, r.site) }}</td><td><span class="pill {{ r.status }}">{{ r.status }}</span></td><td>{{ r.found }}</td><td>{{ r.new_items }}</td><td class="small">{{ r.message }}</td><td class="small mut">{{ r.at[:16] }}</td></tr>{% endfor %}
 {% if not p.latest %}<tr><td colspan="6" class="mut">not run yet</td></tr>{% endif %}
 </table>
 <h2>Cases ({{ p.cases|length }})</h2>
 <table><tr><th>Site</th><th>Case</th><th>Court</th><th>Type</th><th>Party / DOB shown</th><th>Rows</th><th>Last change</th><th>Fetch</th></tr>
 {% for c in p.cases %}<tr><td>{{ c.site }}</td><td><a href="/case/{{ c.id }}">{{ c.case_number }}</a> <a href="{{ c.url }}" target="_blank" class="small">↗</a></td><td class="small">{{ c.court }}</td><td class="small">{{ c.title }}</td><td class="small">{{ c.party_name }} {{ c.dob_shown }}</td><td>{{ c.rows }}</td><td class="small">{{ c.last_change[:16] }}</td><td class="small {{ 'bad' if c.last_fetch_status=='failed' else '' }}">{{ c.last_fetch_status }} {{ c.last_fetch_error[:80] }}</td></tr>{% endfor %}
 </table>
 <h2>Run history (last 30)</h2>
 <table><tr><th>When</th><th>Site</th><th>Status</th><th>Found</th><th>New</th><th>Message</th></tr>
 {% for r in p.history %}<tr><td class="small mut">{{ r.at[:16] }}</td><td>{{ r.site }}</td><td><span class="pill {{ r.status }}">{{ r.status }}</span></td><td>{{ r.found }}</td><td>{{ r.new_items }}</td><td class="small">{{ r.message[:160] }}</td></tr>{% endfor %}
 </table>
</details></div>
{% endfor %}
{% endblock %}"""

CASE = r"""{% extends "base" %}{% block body %}
<h1>{{ c.case_number }} <span class="mut">{{ c.site }}</span></h1>
<div class="card"><div><b>{{ c.court }}</b> · {{ c.title }} · <a href="{{ c.url }}" target="_blank">open at the court ↗</a></div>
<div class="small mut">person: {{ person.last }}, {{ person.first }} · first seen {{ c.first_seen[:16] }} · last change {{ c.last_change[:16] }} · fetch: {{ c.last_fetch_status }} {{ c.last_fetch_error }}</div>
<table>{% for k,v in header.items() %}<tr><th style="width:180px">{{ k }}</th><td>{{ v }}</td></tr>{% endfor %}</table></div>
{% for kind, rows in groups.items() %}
<h2>{{ kind }} ({{ rows|length }})</h2>
<div class="card"><table><tr><th style="width:110px">Date</th><th>Row (verbatim)</th><th class="small" style="width:150px">first seen / source</th></tr>
{% for e in rows %}<tr><td>{{ e.date }}</td><td class="small">{{ e.text }}</td><td class="small mut">{{ e.first_seen[:10] }}<br><a href="/snapshot?path={{ e.snapshot_path|urlencode }}" target="_blank">page copy</a> · {{ e.snapshot_sha[:10] }}</td></tr>{% endfor %}
</table></div>
{% endfor %}
{% endblock %}"""

NAMES = r"""{% extends "base" %}{% block body %}
<h1>Names</h1>
<div class="card"><form method="post" action="/names/add">
<div class="row"><input type="text" name="last" placeholder="Last name" required><input type="text" name="first" placeholder="First name" required><input type="text" name="middle" placeholder="Middle (optional)">
<input type="text" name="dob" placeholder="DOB MM/DD/YYYY (optional)" pattern="\d{2}/\d{2}/\d{4}"><input type="text" name="note" placeholder="Note (optional)"></div>
<div class="row"><label class="chk"><input type="checkbox" name="is_me" value="1"> this is me (export to EDISON)</label>
<label class="chk"><input type="checkbox" name="run_now" value="1" checked> search all sites now for past &amp; existing cases</label><button>Add &amp; monitor</button></div>
<div class="small mut">DOB is optional but without it name-only matches cannot be verified as the same person; DPS and Justice Courts need a DOB to search at all.</div>
</form></div>

{% for p in people %}
<div class="card">
<details {% if open_id==p.id %}open{% endif %}>
<summary>{{ p.last }}, {{ p.first }} {{ p.middle }} {% if p.dob %}<span class="mut">DOB {{ p.dob }}</span>{% else %}<span class="warn">no DOB</span>{% endif %}
 {% if p.is_me %}<span class="pill found">me</span>{% endif %}{% if not p.active %}<span class="pill skipped">paused</span>{% endif %} {{ badges(p.badges) }}
 <span class="mut small">· {{ p.cases|length }} cases · {{ p.entries_total }} rows · last update {{ p.last_update or 'none' }} · {{ p.unread }} unread alerts</span></summary>

 <h2>Edit</h2>
 <form method="post" action="/names/edit"><input type="hidden" name="id" value="{{ p.id }}">
 <div class="row"><input type="text" name="last" value="{{ p.last }}" placeholder="Last" required><input type="text" name="first" value="{{ p.first }}" placeholder="First" required>
 <input type="text" name="middle" value="{{ p.middle }}" placeholder="Middle"><input type="text" name="dob" value="{{ p.dob }}" placeholder="DOB MM/DD/YYYY" pattern="\d{2}/\d{2}/\d{4}">
 <input type="text" name="note" value="{{ p.note }}" placeholder="Note" style="flex:1"></div>
 <div class="row"><label class="chk"><input type="checkbox" name="is_me" value="1" {{ 'checked' if p.is_me }}> this is me (export to EDISON)</label>
 <label class="chk"><input type="checkbox" name="rerun" value="1" checked> re-search all sites if the name or DOB changed</label>
 <button>Save</button></div>
 </form>
 <div class="row"><form method="post" action="/names/toggle"><input type="hidden" name="id" value="{{ p.id }}"><button class="alt">{{ 'pause monitoring' if p.active else 'resume monitoring' }}</button></form>
 <form method="post" action="/run"><input type="hidden" name="person_id" value="{{ p.id }}"><button class="alt">run all sites now</button></form>
 {% if p.badges.new_info %}<form method="post" action="/names/checked"><input type="hidden" name="id" value="{{ p.id }}"><button class="alt">mark checked ({{ p.badges.new_info }} new)</button></form>{% endif %}</div>

 <h2>Custody status</h2>
 <table><tr><th>When</th><th>Source</th><th>In custody</th><th>Bond</th><th>Booking</th><th>Details</th></tr>
 {% for c in p.custody_history %}<tr><td class="small mut">{{ c.seen_at[:16] }}</td><td>{{ c.source }}</td><td>{{ 'YES' if c.in_custody else 'no' }}</td><td>{{ c.bond_raw }}</td><td>{{ c.booking_no }}</td><td class="small">{{ c.details[:200] }}</td></tr>{% endfor %}
 {% if not p.custody_history %}<tr><td colspan="6" class="mut">no custody records. Maricopa County's inmate lookup (<a href="https://www.mcso.org/InmateInfo" target="_blank">mcso.org/InmateInfo ↗</a>) needs its CAPTCHA box ticked by a person - run it there and import the result page (site: Maricopa inmate page) to record custody and bond.</td></tr>{% endif %}</table>

 <h2>Latest run - per site</h2>
 <table><tr><th>Site</th><th>Status</th><th>Found</th><th>New</th><th>Message</th><th>When</th></tr>
 {% for r in p.latest %}<tr><td>{{ site_labels.get(r.site, r.site) }}</td><td><span class="pill {{ r.status }}">{{ r.status }}</span></td><td>{{ r.found }}</td><td>{{ r.new_items }}</td><td class="small">{{ r.message }}</td><td class="small mut">{{ r.at[:16] }}</td></tr>{% endfor %}
 {% if not p.latest %}<tr><td colspan="6" class="mut">not run yet</td></tr>{% endif %}</table>

 <h2>Cases ({{ p.cases|length }})</h2>
 <table><tr><th>Site</th><th>Case</th><th>Court</th><th>Type</th><th>Party / DOB shown</th><th>Rows</th><th>Last change</th></tr>
 {% for c in p.cases %}<tr><td>{{ c.site }}</td><td><a href="/case/{{ c.id }}">{{ c.case_number }}</a> <a href="{{ c.url }}" target="_blank" class="small">↗</a></td><td class="small">{{ c.court }}</td><td class="small">{{ c.title }}</td><td class="small">{{ c.party_name }} {{ c.dob_shown }}</td><td>{{ c.rows }}</td><td class="small">{{ c.last_change[:16] }}</td></tr>{% endfor %}
 {% if not p.cases %}<tr><td colspan="7" class="mut">no cases captured</td></tr>{% endif %}</table>

 <h2>Warrant checks (DPS)</h2>
 <table><tr><th>When</th><th>Status</th><th>Page text</th></tr>
 {% for w in p.warrants %}<tr><td class="small mut">{{ w.checked_at[:16] }}</td><td><span class="pill {{ w.status }}">{{ w.status }}</span></td><td class="small">{{ w.text[:200] }} <a href="/snapshot?path={{ w.snapshot_path|urlencode }}" target="_blank">page copy</a></td></tr>{% endfor %}
 {% if not p.warrants %}<tr><td colspan="3" class="mut">none yet{% if not p.dob %} (needs a DOB){% endif %}</td></tr>{% endif %}</table>

 <h2>Jail roster matches (MCSO)</h2>
 <table><tr><th>Booking #</th><th>Name on roster</th><th>DOB</th><th>Status</th><th>Booked</th><th>Still listed</th></tr>
 {% for m in p.arrests %}<tr><td><a href="{{ m.detail_url }}" target="_blank">{{ m.booking_no }}</a></td><td>{{ m.name }}</td><td>{{ m.dob }}</td><td>{{ m.status }}</td><td>{{ m.booking_date }}</td><td>{{ 'no - gone ' + m.gone_at[:10] if m.gone_at else 'yes' }}</td></tr>{% endfor %}
 {% if not p.arrests %}<tr><td colspan="6" class="mut">no matches on any roster pull</td></tr>{% endif %}</table>

 <h2>Alerts (last 40)</h2>
 {% for a in p.alerts %}<div class="alert {{ a.kind }}"><span class="mut small">{{ a.created_at[:16] }} · {{ a.site }} · {{ a.kind }}</span><br>{{ a.text }} {% if a.url %}<a href="{{ a.url }}" target="_blank">open</a>{% endif %}</div>{% endfor %}
 {% if not p.alerts %}<div class="mut">none</div>{% endif %}
</details></div>
{% endfor %}
{% endblock %}"""

SEARCH = r"""{% extends "base" %}{% block body %}
<h1>Search everywhere</h1>
<div class="card"><form method="post" action="/search/live">
<div class="row"><input type="text" name="last" placeholder="Last name" value="{{ q.last }}" required><input type="text" name="first" placeholder="First name" value="{{ q.first }}" required><input type="text" name="dob" placeholder="DOB MM/DD/YYYY (optional)" value="{{ q.dob }}"></div>
<div class="row">{% for k,l in live_sites %}<label class="chk"><input type="checkbox" name="sites" value="{{ k }}" checked> {{ l }}</label>{% endfor %}</div>
<div class="row"><label class="chk"><input type="checkbox" name="save" value="1"> save results into Monitoring (adds the name to the list)</label><button>Search live sites</button></div>
<div class="small mut">AZ Courts public access can't be searched from here (image CAPTCHA) - <a href="https://apps.azcourts.gov/publicaccess/caselookup.aspx" target="_blank">open it ↗</a> and use Import for the saved page.</div>
</form></div>
<h2>Local search (everything already stored)</h2>
<div class="card"><form method="get" action="/search">
<div class="row"><input type="text" name="q" placeholder="name, case number, charge, address, booking #..." value="{{ q.q }}" style="flex:1">
{% for k,l in local_sites %}<label class="chk"><input type="checkbox" name="local" value="{{ k }}" {{ 'checked' if k in q.local }}> {{ l }}</label>{% endfor %}<button class="alt">Search stored data</button></div></form>
{% if results is not none %}
{% for section, rows in results.items() %}<h2>{{ section }} ({{ rows|length }})</h2>
<table>{% for r in rows %}<tr>{% for v in r %}<td class="small">{{ v|safe }}</td>{% endfor %}</tr>{% endfor %}{% if not rows %}<tr><td class="mut">none</td></tr>{% endif %}</table>{% endfor %}
{% endif %}</div>
{% endblock %}"""

WARRANTS = r"""{% extends "base" %}{% block body %}
<h1>Warrant checks (AZ DPS)</h1>
<div class="card"><form method="post" action="/warrants/check">
<div class="row"><select name="person_id">{% for p in people %}<option value="{{ p.id }}" {{ 'selected' if p.id==pid }}>{{ p.last }}, {{ p.first }} {{ p.dob }}</option>{% endfor %}</select><button>Check now</button></div>
<div class="row"><input type="text" name="last" placeholder="or: Last name"><input type="text" name="first" placeholder="First name"><input type="text" name="dob" placeholder="DOB MM/DD/YYYY (required by DPS)"></div>
<div class="small mut">DPS returns at most 5 results and requires first name, last name and date of birth. "none" below means the DPS page showed its "Results Not Found" block for that exact query. <a href="https://www.azdps.gov/warrant-search" target="_blank">DPS page ↗</a></div></form></div>
<div class="card"><table><tr><th>When</th><th>Person</th><th>Status</th><th>Page text</th><th></th></tr>
{% for w in checks %}<tr><td class="small mut">{{ w.checked_at[:16] }}</td><td>{{ people_names.get(w.person_id, w.person_id) }}</td><td><span class="pill {{ w.status }}">{{ w.status }}</span></td><td class="small">{{ w.text[:300] }}</td><td class="small"><a href="/snapshot?path={{ w.snapshot_path|urlencode }}" target="_blank">page copy</a></td></tr>{% endfor %}
</table></div>
{% endblock %}"""

ARRESTS = r"""{% extends "base" %}{% block body %}
<h1>Arrests</h1>
<div class="card"><b>Jail roster - portal.mobileso.com</b> <span class="warn small">(this page is the Mobile County Sheriff, Alabama, roster - not Maricopa)</span> · <a href="https://www.mcso.org/InmateInfo" target="_blank">Maricopa County inmate lookup ↗</a> (CAPTCHA - import the result page) <span class="mut small">last pull: {{ mcso.at if mcso else 'never' }} · {{ mcso.title if mcso else '' }} · {{ mcso.rows if mcso else 0 }} rows ({{ mcso.new_rows if mcso else 0 }} new that pull) · {{ roster_total }} bookings retained locally</span>
<h2>Matches against the names list</h2>
<table><tr><th>Person</th><th>Booking #</th><th>Name on roster</th><th>DOB</th><th>Status</th><th>Booked</th><th>Expected release</th><th>Still listed</th></tr>
{% for m in matches %}<tr><td>{{ people_names.get(m.person_id) }}</td><td><a href="{{ m.detail_url }}" target="_blank">{{ m.booking_no }}</a></td><td>{{ m.name }}</td><td>{{ m.dob }}</td><td>{{ m.status }}</td><td>{{ m.booking_date }}</td><td>{{ m.expected_release }}</td><td>{{ 'no - gone ' + m.gone_at[:10] if m.gone_at else 'yes' }}</td></tr>{% endfor %}
{% if not matches %}<tr><td colspan="8" class="mut">No one on the names list appears on the roster pulls so far.</td></tr>{% endif %}</table>
<form method="get" action="/arrests"><div class="row"><input type="text" name="mq" placeholder="search roster: LAST,FIRST or booking # or DOB" value="{{ mq }}" style="flex:1"><button class="alt">Search roster</button></div></form>
{% if mrows is not none %}<table><tr><th>Booking #</th><th>Name</th><th>DOB</th><th>Status</th><th>Booked</th><th>First seen</th><th>Last seen</th></tr>
{% for r in mrows %}<tr><td><a href="{{ r.detail_url }}" target="_blank">{{ r.booking_no }}</a></td><td>{{ r.name }}</td><td>{{ r.dob }}</td><td>{{ r.status }}</td><td>{{ r.booking_date }}</td><td class="small mut">{{ r.first_seen[:10] }}</td><td class="small mut">{{ r.last_seen[:10] }}{{ ' (gone)' if r.gone_at }}</td></tr>{% endfor %}</table>{% endif %}
</div>
<div class="card"><b>Chandler PD arrest bookings (open data CSV)</b> <span class="mut small">last pull: {{ ch.at if ch else 'never' }} · {{ ch.rows if ch else 0 }} rows in file · {{ ch_total }} retained locally</span>
<div class="warn small">This dataset has no names - only an arrestee number, age, race, gender - so it cannot be matched to anyone on the list. Search it by date, charge, address or report number.</div>
<form method="get" action="/arrests"><div class="row"><input type="text" name="cq" placeholder="search Chandler: charge / address / report # / date YYYY-MM-DD" value="{{ cq }}" style="flex:1"><button class="alt">Search Chandler</button></div></form>
{% if crows is not none %}<table><tr><th>Arrest #</th><th>Date</th><th>Time</th><th>Charge</th><th>Address</th><th>City</th><th>Report #</th><th>Age</th></tr>
{% for r in crows %}<tr><td>{{ r.arrest_number }}</td><td>{{ r.arrest_date }}</td><td>{{ r.arrest_time }}</td><td class="small">{{ r.arrest_charge }}</td><td class="small">{{ r.arrest_address }}</td><td>{{ r.arrest_city }}</td><td>{{ r.related_offense_report_number }}</td><td>{{ r.age }}</td></tr>{% endfor %}</table>{% endif %}
</div>
<div class="card"><b>Power BI report captures</b> <span class="mut small">{{ pbi|length }} capture(s)</span>
<form method="get" action="/arrests"><div class="row"><input type="text" name="pq" placeholder="search captured report text" value="{{ pq }}" style="flex:1"><button class="alt">Search captures</button></div></form>
<table><tr><th>When</th><th>Pages</th><th>Data responses</th><th>Folder</th></tr>{% for p in pbi %}<tr><td class="small mut">{{ p.at[:16] }}</td><td>{{ p.pages }}</td><td>{{ p.responses }}</td><td class="small">{{ p.dir }}</td></tr>{% endfor %}</table>
{% if prows is not none %}{% for r in prows %}<pre>{{ r }}</pre>{% endfor %}{% endif %}
</div>
{% endblock %}"""

PAYMENTS = r"""{% extends "base" %}{% block body %}
<h1>Payments &amp; compliance</h1>
<p class="mut small">Straight from the court pages: balance / installment rows and payment history for every case that shows them. Nothing here is computed - if a court page does not show a due date, none is shown.</p>
{% for c in cases %}
<div class="card"><b><a href="/case/{{ c.id }}">{{ c.case_number }}</a></b> · {{ c.court }} · {{ people_names.get(c.person_id) }} · <a href="{{ c.url }}" target="_blank">court page ↗</a>
<table>{% for e in c.rows %}<tr><td style="width:110px">{{ e.kind }}</td><td class="small">{{ e.text }}</td><td class="small mut" style="width:100px">seen {{ e.first_seen[:10] }}</td></tr>{% endfor %}</table>
{% if c.reminders %}<div class="row">{% for r in c.reminders %}<a class="btn alt" href="{{ r.ics }}">📅 reminder: {{ r.label }}</a>{% endfor %}</div>{% endif %}
</div>
{% endfor %}
{% if not cases %}<div class="card mut">No balance or payment rows captured yet.</div>{% endif %}
{% endblock %}"""

IMPORT = r"""{% extends "base" %}{% block body %}
<h1>Import a saved page</h1>
<div class="card"><p class="small">For sites this watcher cannot search on its own (AZ Courts public access has an image CAPTCHA): run the search in your browser, save the results or case page (Ctrl+S → "Webpage, Complete" or .html), and upload it here. Every table on the page is stored verbatim under the person and case you choose.</p>
<form method="post" action="/import" enctype="multipart/form-data">
<div class="row"><select name="person_id">{% for p in people %}<option value="{{ p.id }}">{{ p.last }}, {{ p.first }}</option>{% endfor %}</select>
<select name="site"><option value="azcourts">AZ Courts public access</option><option value="maricopa_inmate">Maricopa inmate page (mcso.org/InmateInfo)</option><option value="other">other site</option></select>
<input type="text" name="case_number" placeholder="Case number (as printed)" required><input type="text" name="court" placeholder="Court name (as printed)"><input type="text" name="note" placeholder="Note"></div>
<div class="row"><input type="file" name="file" accept=".html,.htm,.mhtml,.txt" required><button>Import</button></div></form></div>
<div class="card"><table><tr><th>When</th><th>Site</th><th>Person</th><th>File</th><th>Rows</th><th>Note</th></tr>
{% for i in imports %}<tr><td class="small mut">{{ i.at[:16] }}</td><td>{{ i.site }}</td><td>{{ people_names.get(i.person_id) }}</td><td class="small">{{ i.filename }}</td><td>{{ i.rows }}</td><td class="small">{{ i.note }}</td></tr>{% endfor %}</table></div>
{% endblock %}"""

JOB = r"""{% extends "base" %}{% block body %}
<h1>Job {{ job.id }} <span class="pill {{ 'found' if job.status=='done' else ('failed' if job.status=='failed' else 'skipped') }}">{{ job.status }}</span></h1>
{% if job.status=='running' %}<meta http-equiv="refresh" content="5">{% endif %}
<div class="card"><div class="small mut">{{ job.desc }} · started {{ job.started }}</div><pre>{{ job.log }}</pre></div>
{% if job.result_html %}<div class="card">{{ job.result_html|safe }}</div>{% endif %}
{% endblock %}"""

LOGIN = r"""{% extends "base" %}{% block body %}
<h1>Court Watch</h1><div class="card"><form method="post" action="/login"><div class="row"><input type="password" name="pin" placeholder="PIN" autofocus><button>Enter</button></div></form></div>
{% endblock %}"""
