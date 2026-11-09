"""Local SQLite log of applications, so nothing gets applied to twice."""

import sqlite3
from datetime import date, datetime

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    job_id     TEXT PRIMARY KEY,
    title      TEXT,
    company    TEXT,
    url        TEXT,
    status     TEXT NOT NULL,      -- drafted | held | submitted | skipped
    updated_at TEXT NOT NULL,
    notes      TEXT
);
-- Old table: days Wellfound said its limit on active applications was reached.
CREATE TABLE IF NOT EXISTS limit_hits (day TEXT PRIMARY KEY);
-- Days a site blocked applying, per site.
CREATE TABLE IF NOT EXISTS site_limit_hits (site TEXT NOT NULL, day TEXT NOT NULL, PRIMARY KEY (site, day));
INSERT OR IGNORE INTO site_limit_hits (site, day) SELECT 'wellfound', day FROM limit_hits;
"""

# Job ids carry their site: Indeed's start with 'indeed:', Wellfound's have no prefix.
SITE_WHERE = {"indeed": "job_id LIKE 'indeed:%'", "wellfound": "job_id NOT LIKE '%:%'"}


def site_of(job_id: str) -> str:
    return "indeed" if job_id.startswith("indeed:") else "wellfound"


def caps(site: str) -> tuple[int, int]:
    """(start, max) of the daily cap for a site."""
    if site == "indeed":
        return config.INDEED_DAILY_START, config.INDEED_DAILY_MAX
    return config.DAILY_START, config.DAILY_MAX


def _conn() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def record(job_id: str, status: str, title: str = "", company: str = "", url: str = "", notes: str = "") -> None:
    with _conn() as c:
        c.execute(
            """INSERT INTO applications (job_id, title, company, url, status, updated_at, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(job_id) DO UPDATE SET
                 status = excluded.status, updated_at = excluded.updated_at,
                 title = COALESCE(NULLIF(excluded.title, ''), title),
                 company = COALESCE(NULLIF(excluded.company, ''), company),
                 url = COALESCE(NULLIF(excluded.url, ''), url),
                 notes = COALESCE(NULLIF(excluded.notes, ''), notes)""",
            (job_id, title, company, url, status, datetime.now().isoformat(timespec="seconds"), notes),
        )


def get(job_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_all(status: str | None = None, limit: int = 50, site: str | None = None) -> list[dict]:
    q, where, args = "SELECT job_id, title, company, status, updated_at FROM applications", [], []
    if status:
        where.append("status = ?")
        args.append(status)
    if site:
        where.append(SITE_WHERE[site])
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY updated_at DESC LIMIT ?"
    args.append(limit)
    with _conn() as c:
        return [dict(r) for r in c.execute(q, args)]


def submitted_today(site: str = "wellfound") -> int:
    with _conn() as c:
        return c.execute(
            f"SELECT COUNT(*) FROM applications WHERE status = 'submitted' AND updated_at LIKE ? AND {SITE_WHERE[site]}",
            (f"{date.today().isoformat()}%",),
        ).fetchone()[0]


def record_limit_hit(site: str = "wellfound") -> None:
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO site_limit_hits (site, day) VALUES (?, ?)", (site, date.today().isoformat()))


def daily_limit(site: str = "wellfound") -> dict:
    """Today's cap on a site: its start value plus one for each clean past day of
    applying there since it last blocked you, up to its max."""
    today = date.today().isoformat()
    start, top = caps(site)
    with _conn() as c:
        last_hit = c.execute("SELECT MAX(day) FROM site_limit_hits WHERE site = ?", (site,)).fetchone()[0] or ""
        clean_days = c.execute(
            f"""SELECT COUNT(DISTINCT substr(updated_at, 1, 10)) FROM applications
               WHERE status = 'submitted' AND substr(updated_at, 1, 10) > ?
                 AND substr(updated_at, 1, 10) < ? AND {SITE_WHERE[site]}""",
            (last_hit, today),
        ).fetchone()[0]
    return {"submitted_today": submitted_today(site), "limit_today": min(top, start + clean_days)}


def delete(job_id: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM applications WHERE job_id = ?", (job_id,))
