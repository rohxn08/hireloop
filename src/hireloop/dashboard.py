"""Local, read-only dashboard over the application log.

Run with: uv run hireloop-dashboard   (opens http://127.0.0.1:8765)
It only listens on 127.0.0.1, reads data/applications.db, and never changes it.
"""

import json
import os
import sqlite3
import sys
import webbrowser
from collections import Counter
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, sites, store

DAYS = 30
PORT = config.DASHBOARD_PORT


def stats() -> dict:
    """Everything the page shows, computed from the local log."""
    if config.DB_PATH.exists():
        with sqlite3.connect(config.DB_PATH) as c:
            c.row_factory = sqlite3.Row
            rows = [dict(r) for r in c.execute(
                "SELECT job_id, title, company, url, status, updated_at, notes FROM applications ORDER BY updated_at DESC")]
    else:
        rows = []

    today = date.today()
    submitted = [r for r in rows if r["status"] == "submitted"]
    per_day = Counter(r["updated_at"][:10] for r in submitted)
    week_start = (today - timedelta(days=6)).isoformat()
    for r in rows:
        site = sites.of(r["job_id"])
        r["site"] = site.LABEL
        r["url"] = r["url"] or site.job_url(r["job_id"])
    caps = {}
    for name, site in sites.SITES.items():
        cap = store.daily_limit(name) if config.DB_PATH.exists() else {"submitted_today": 0, "limit_today": store.caps(name)[0]}
        caps[site.LABEL] = {**cap, "max": store.caps(name)[1]}
    return {
        "today": today.isoformat(),
        "totals": {
            "submitted": len(submitted),
            "this_week": sum(n for d, n in per_day.items() if d >= week_start),
            "drafted": sum(r["status"] == "drafted" for r in rows),
            "skipped": sum(r["status"] == "skipped" for r in rows),
            "held": sum(r["status"] == "held" for r in rows),
        },
        # Combined across sites, plus each site's own cap.
        "cap": {"submitted_today": sum(c["submitted_today"] for c in caps.values()),
                "limit_today": sum(c["limit_today"] for c in caps.values())},
        "caps": caps,
        "applied_pages": {s.LABEL: s.APPLIED_URL for s in sites.SITES.values()},
        "per_day": [{"day": (d := (today - timedelta(days=i)).isoformat()), "count": per_day.get(d, 0)}
                    for i in range(DAYS - 1, -1, -1)],
        "by_site": dict(Counter(r["site"] for r in submitted)),
        "top_companies": Counter(r["company"] or "Unknown" for r in submitted).most_common(8),
        "skip_reasons": Counter((r["notes"] or "No reason given") for r in rows if r["status"] == "skipped").most_common(8),
        "rows": rows,
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/api/stats":
            body, ctype = json.dumps(stats()).encode(), "application/json"
        elif self.path in ("/", "/index.html"):
            body, ctype = PAGE.encode(), "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass  # keep the terminal quiet


def main() -> None:
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        print(f"Port {PORT} is busy. Set HIRELOOP_DASHBOARD_PORT to another port.", file=sys.stderr)
        sys.exit(1)
    url = f"http://127.0.0.1:{PORT}"
    print(f"Dashboard: {url}  (Ctrl+C to stop)")
    if "--no-open" not in sys.argv:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hireloop Applications</title>
<style>
:root {
  color-scheme: light;
  --surface-0: #f4f3f0; --surface-1: #fcfcfb; --border: #e3e2dd;
  --text-primary: #0b0b0b; --text-secondary: #52514e; --text-muted: #6a6964;
  --grid: #ebeae6; --series-1: #2a78d6; --series-1-hover: #1c5cab;
  --good: #0ca30c; --warning: #fab219; --link: #256abf;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --surface-0: #121211; --surface-1: #1a1a19; --border: #2e2e2c;
    --text-primary: #f0efec; --text-secondary: #c3c2b7; --text-muted: #8f8e88;
    --grid: #2a2a28; --series-1: #3987e5; --series-1-hover: #6da7ec; --link: #6da7ec;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface-0: #121211; --surface-1: #1a1a19; --border: #2e2e2c;
  --text-primary: #f0efec; --text-secondary: #c3c2b7; --text-muted: #8f8e88;
  --grid: #2a2a28; --series-1: #3987e5; --series-1-hover: #6da7ec; --link: #6da7ec;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--surface-0); color: var(--text-primary);
  font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1100px; margin: 0 auto; padding: 24px 16px 48px; }
header { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
h1 { font-size: 22px; margin: 0; }
h2 { font-size: 15px; margin: 0 0 12px; }
.muted { color: var(--text-muted); }
.card { background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 20px 0; }
.tile .label { color: var(--text-secondary); font-size: 13px; }
.tile .value { font-size: 30px; font-weight: 650; font-variant-numeric: tabular-nums; line-height: 1.2; }
.tile .sub { color: var(--text-muted); font-size: 12px; }
.meter { height: 6px; border-radius: 3px; background: var(--grid); margin-top: 8px; overflow: hidden; }
.meter > div { height: 100%; background: var(--series-1); border-radius: 3px; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 12px; }
@media (max-width: 720px) { .grid2 { grid-template-columns: 1fr; } }
#chart { position: relative; }
#chart svg { display: block; width: 100%; height: 220px; }
.tip { position: absolute; pointer-events: none; background: var(--surface-1); border: 1px solid var(--border);
  border-radius: 6px; padding: 4px 8px; font-size: 12px; white-space: nowrap; box-shadow: 0 2px 8px rgb(0 0 0 / .12); display: none; }
ol.rank { margin: 0; padding: 0; list-style: none; }
ol.rank li { display: flex; justify-content: space-between; gap: 12px; padding: 6px 0; border-bottom: 1px solid var(--grid); }
ol.rank li:last-child { border-bottom: 0; }
ol.rank .n { font-variant-numeric: tabular-nums; color: var(--text-secondary); }
.filters { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 12px; }
.filters button { font: inherit; padding: 4px 12px; border-radius: 999px; border: 1px solid var(--border);
  background: transparent; color: var(--text-secondary); cursor: pointer; }
.filters button[aria-pressed="true"] { background: var(--text-primary); color: var(--surface-1); border-color: var(--text-primary); }
.tablewrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 8px; border-bottom: 1px solid var(--grid); vertical-align: top; }
th { color: var(--text-secondary); font-weight: 600; font-size: 12px; }
td.date { white-space: nowrap; font-variant-numeric: tabular-nums; color: var(--text-secondary); }
a { color: var(--link); }
.status { display: inline-flex; align-items: center; gap: 6px; }
.status::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: var(--dot); }
.status.submitted { --dot: var(--good); } .status.drafted { --dot: var(--warning); } .status.skipped { --dot: var(--text-muted); } .status.held { --dot: #d03b3b; }
.empty { padding: 24px; text-align: center; color: var(--text-muted); }
</style>
</head>
<body>
<main>
  <header>
    <h1>Job applications</h1>
    <span class="muted" id="updated"></span>
  </header>

  <section class="tiles" id="tiles"></section>

  <section class="card">
    <h2>Applications sent per day <span class="muted">· last 30 days</span></h2>
    <div id="chart"><div class="tip" id="tip"></div></div>
  </section>

  <section class="grid2">
    <div class="card"><h2>Companies applied to most</h2><ol class="rank" id="companies"></ol></div>
    <div class="card"><h2>Why jobs were skipped</h2><ol class="rank" id="reasons"></ol></div>
  </section>

  <section class="card" style="margin-top:12px">
    <h2>All jobs</h2>
    <div class="filters" role="group" aria-label="Filter by status" id="filters"></div>
    <div class="filters" role="group" aria-label="Filter by site" id="sitefilters"></div>
    <div class="tablewrap"><table>
      <thead><tr><th>Date</th><th>Site</th><th>Role</th><th>Company</th><th>Status</th><th>Note</th></tr></thead>
      <tbody id="rows"></tbody>
    </table></div>
  </section>
  <p class="muted" style="margin-top:16px" id="applied"></p>
</main>
<script>
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmtDay = (iso) => new Date(iso + "T00:00:00").toLocaleDateString(undefined, {day: "numeric", month: "short"});
let data, filter = "all", siteFilter = "all";

function tiles(d) {
  const t = d.totals, cap = d.cap;
  const pct = Math.min(100, Math.round(100 * cap.submitted_today / Math.max(1, cap.limit_today)));
  $("tiles").innerHTML = `
    <div class="card tile"><div class="label">Applied in total</div><div class="value">${t.submitted}</div>
      <div class="sub">${t.this_week} in the last 7 days${Object.keys(d.by_site).length > 1 ? " · " + Object.entries(d.by_site).map(([s, n]) => `${esc(s)} ${n}`).join(", ") : ""}</div></div>
    <div class="card tile"><div class="label">Applied today</div><div class="value">${cap.submitted_today} <span class="muted" style="font-size:16px">of ${cap.limit_today}</span></div>
      <div class="meter" role="img" aria-label="${cap.submitted_today} of ${cap.limit_today} used today"><div style="width:${pct}%"></div></div>
      <div class="sub">${Object.entries(d.caps).map(([s, c]) => `${esc(s)} ${c.submitted_today}/${c.limit_today} (max ${c.max})`).join(" · ")}</div></div>
    <div class="card tile"><div class="label">Held for your review</div><div class="value">${t.held}</div>
      <div class="sub">${t.drafted} more drafted, not sent</div></div>
    <div class="card tile"><div class="label">Skipped</div><div class="value">${t.skipped}</div>
      <div class="sub">Hidden from search</div></div>`;
}

function chart(points) {
  const W = 1000, H = 220, L = 28, R = 8, T = 12, B = 26;
  const max = Math.max(4, ...points.map(p => p.count));
  const step = Math.max(1, Math.ceil(max / 4)), top = step * 4;
  const slot = (W - L - R) / points.length, bw = Math.max(4, slot - 2);   // 2px gap between bars
  const y = v => T + (H - T - B) * (1 - v / top);
  let g = "";
  for (let v = 0; v <= top; v += step)
    g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--grid)" stroke-width="1"/>
          <text x="${L - 6}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="var(--text-muted)">${v}</text>`;
  let bars = "", labels = "";
  points.forEach((p, i) => {
    const x = L + i * slot + 1, h = y(0) - y(p.count);
    if (p.count > 0) {
      const r = Math.min(4, h, bw / 2);   // rounded data-end, flat on the baseline
      bars += `<path class="bar" data-i="${i}" d="M${x},${y(0)} V${y(p.count) + r} q0,-${r} ${r},-${r} H${x + bw - r} q${r},0 ${r},${r} V${y(0)} Z" fill="var(--series-1)"/>`;
    }
    bars += `<rect class="hit" data-i="${i}" x="${L + i * slot}" y="${T}" width="${slot}" height="${H - T - B}" fill="transparent"/>`;
    if (i % 5 === 0 || i === points.length - 1)
      labels += `<text x="${x + bw / 2}" y="${H - 8}" text-anchor="middle" font-size="11" fill="var(--text-muted)">${fmtDay(p.day)}</text>`;
  });
  const total = points.reduce((a, p) => a + p.count, 0);
  $("chart").insertAdjacentHTML("afterbegin",
    `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${total} applications in the last 30 days">${g}${bars}${labels}</svg>`);
  const svg = $("chart").querySelector("svg"), tip = $("tip");
  svg.addEventListener("pointermove", e => {
    const hit = e.target.closest(".hit");
    svg.querySelectorAll(".bar").forEach(b => b.setAttribute("fill", "var(--series-1)"));
    if (!hit) { tip.style.display = "none"; return; }
    svg.querySelector(`.bar[data-i="${hit.dataset.i}"]`)?.setAttribute("fill", "var(--series-1-hover)");
    const p = points[+hit.dataset.i];
    tip.innerHTML = `<b>${p.count}</b> applied · ${fmtDay(p.day)}`;
    tip.style.display = "block";
    const box = $("chart").getBoundingClientRect();
    tip.style.left = Math.min(box.width - tip.offsetWidth, Math.max(0, e.clientX - box.left - tip.offsetWidth / 2)) + "px";
    tip.style.top = (e.clientY - box.top - 36) + "px";
  });
  svg.addEventListener("pointerleave", () => {
    tip.style.display = "none";
    svg.querySelectorAll(".bar").forEach(b => b.setAttribute("fill", "var(--series-1)"));
  });
}

function ranks(id, items, empty) {
  $(id).innerHTML = items.length
    ? items.map(([k, n]) => `<li><span>${esc(k)}</span><span class="n">${n}</span></li>`).join("")
    : `<li class="muted">${empty}</li>`;
}

function table() {
  const rows = data.rows.filter(r => (filter === "all" || r.status === filter) && (siteFilter === "all" || r.site === siteFilter));
  $("rows").innerHTML = rows.length ? rows.map(r => `<tr>
      <td class="date">${esc(r.updated_at.slice(0, 10))}</td>
      <td>${esc(r.site)}</td>
      <td><a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title || r.job_id)}</a></td>
      <td>${esc(r.company)}</td>
      <td><span class="status ${esc(r.status)}">${esc(r.status)}</span></td>
      <td class="muted">${esc(r.notes)}</td></tr>`).join("")
    : `<tr><td colspan="6" class="empty">Nothing here yet.</td></tr>`;
}

function filters() {
  const counts = {all: data.rows.length};
  data.rows.forEach(r => counts[r.status] = (counts[r.status] || 0) + 1);
  $("filters").innerHTML = ["all", "submitted", "held", "drafted", "skipped"].map(s =>
    `<button data-s="${s}" aria-pressed="${s === filter}">${s[0].toUpperCase() + s.slice(1)} (${counts[s] || 0})</button>`).join("");
  $("filters").onclick = e => { const b = e.target.closest("button"); if (!b) return; filter = b.dataset.s; filters(); table(); };
  const names = ["all", ...Object.keys(data.caps)];
  $("sitefilters").innerHTML = names.map(s =>
    `<button data-s="${esc(s)}" aria-pressed="${s === siteFilter}">${s === "all" ? "All sites" : esc(s)} (${s === "all" ? data.rows.length : data.rows.filter(r => r.site === s).length})</button>`).join("");
  $("sitefilters").onclick = e => { const b = e.target.closest("button"); if (!b) return; siteFilter = b.dataset.s; filters(); table(); };
}

fetch("/api/stats").then(r => r.json()).then(d => {
  data = d;
  $("updated").textContent = "As of " + new Date().toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"});
  tiles(d); chart(d.per_day);
  ranks("companies", d.top_companies, "No applications yet");
  ranks("reasons", d.skip_reasons, "Nothing skipped yet");
  filters(); table();
  $("applied").innerHTML = "Recruiter replies and application status live on " +
    Object.entries(d.applied_pages).map(([s, u]) => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(s)}'s Applied page</a>`).join(" and ") +
    ". This page only knows what hireloop logged on this computer.";
});
</script>
</body>
</html>
"""
