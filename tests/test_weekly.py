"""Weekly report: content, once-per-week guard, and never sending when off."""

from datetime import date

import pytest

from hireloop import config, notify, store, weekly


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(config, "EMAIL_TO", "me@gmail.com")
    monkeypatch.setattr(config, "SMTP_USER", "me@gmail.com")
    monkeypatch.setattr(config, "SMTP_PASSWORD", "x")


def test_report_content():
    store.record("1-ai", "submitted", title="AI Engineer", company="Acme")
    store.record("2-sw", "skipped", title="SWE", company="Beta", notes="Needs 5+ years")
    store.record("3-cv", "drafted", title="CV Engineer", company="Gamma")
    msg = weekly.build()
    assert msg["Subject"].startswith("[Hireloop] Weekly report: 1 applied")
    body = msg.get_content()
    assert "AI Engineer at Acme" in body and "https://wellfound.com/jobs/1-ai" in body
    assert "1× Needs 5+ years" in body and "CV Engineer at Gamma" in body


def test_sends_once_per_week(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send", sent.append)
    sunday = date(2026, 11, 8)
    assert weekly.send_weekly(today=sunday) == "sent to me@gmail.com"
    assert weekly.send_weekly(today=sunday) == "already sent this week"
    assert weekly.send_weekly(force=True, today=sunday).startswith("sent")
    assert weekly.send_weekly(today=date(2026, 11, 15)).startswith("sent")   # next week
    assert len(sent) == 3


def test_off_without_password(monkeypatch):
    monkeypatch.setattr(config, "SMTP_PASSWORD", "")
    assert weekly.send_weekly().startswith("off")
    assert not (config.DATA_DIR / weekly.SENT_MARKER).exists()
