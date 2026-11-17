"""The daily cap ramps up by one per clean day of applying, and resets on a limit hit."""

import sqlite3
from datetime import date, timedelta

import pytest

from hireloop import config, store


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(config, "DAILY_START", 5)
    monkeypatch.setattr(config, "DAILY_MAX", 7)


def submitted_on(job_id: str, days_ago: int) -> None:
    store.record(job_id, "submitted")
    day = (date.today() - timedelta(days=days_ago)).isoformat()
    with sqlite3.connect(config.DB_PATH) as c:
        c.execute("UPDATE applications SET updated_at = ? WHERE job_id = ?", (f"{day}T10:00:00", job_id))


def test_starts_low():
    assert store.daily_limit() == {"submitted_today": 0, "limit_today": 5}


def test_rises_one_per_clean_day_and_caps():
    submitted_on("a", 3); submitted_on("b", 3); submitted_on("c", 1)
    assert store.daily_limit()["limit_today"] == 7          # 5 + 2 distinct days
    submitted_on("d", 2)
    assert store.daily_limit()["limit_today"] == 7          # capped at DAILY_MAX


def test_today_counts_toward_usage_not_ramp():
    store.record("x", "submitted")
    assert store.daily_limit() == {"submitted_today": 1, "limit_today": 5}


def test_limit_hit_resets_ramp():
    submitted_on("a", 3); submitted_on("b", 2)
    with sqlite3.connect(config.DB_PATH) as c:
        c.execute("INSERT INTO limit_hits VALUES (?)", ((date.today() - timedelta(days=2)).isoformat(),))
    submitted_on("c", 1)
    assert store.daily_limit()["limit_today"] == 6          # only the day after the hit counts


def test_caps_are_per_site(monkeypatch):
    monkeypatch.setattr(config, "INDEED_DAILY_START", 10)
    monkeypatch.setattr(config, "INDEED_DAILY_MAX", 30)
    store.record("1-a", "submitted")
    store.record("indeed:0123456789abcdef", "submitted")
    store.record("indeed:fedcba9876543210", "submitted")
    assert store.daily_limit("wellfound") == {"submitted_today": 1, "limit_today": 5}
    assert store.daily_limit("indeed") == {"submitted_today": 2, "limit_today": 10}
    store.record_limit_hit("indeed")                       # Indeed blocking doesn't touch Wellfound
    assert store.daily_limit("wellfound")["limit_today"] == 5
    assert [r["job_id"] for r in store.list_all(site="wellfound")] == ["1-a"]
    assert len(store.list_all(site="indeed")) == 2
