"""Email alerts: message content, and that failures never raise."""

import smtplib

from hireloop import config, notify

JOB = {"id": "5-ai", "title": "AI Engineer", "company": "Acme", "url": "https://wellfound.com/jobs/5-ai",
       "salary": "₹10L – ₹15L", "location": "Bengaluru"}


def setup(monkeypatch, password="abcd efgh"):
    monkeypatch.setattr(config, "EMAIL_TO", "track@gmail.com")
    monkeypatch.setattr(config, "SMTP_USER", "track@gmail.com")
    monkeypatch.setattr(config, "SMTP_PASSWORD", password)


def test_message_content(monkeypatch):
    setup(monkeypatch)
    msg = notify.build_message(JOB, [{"question": "Why us?", "answer": "Voice agents."}, {"question": "Q2", "answer": ""}])
    assert msg["Subject"] == "[Wellfound] Applied: AI Engineer at Acme"
    assert msg["To"] == "track@gmail.com"
    body = msg.get_content()
    assert "https://wellfound.com/jobs/5-ai" in body and "₹10L – ₹15L" in body
    assert "A: Voice agents." in body and "A: (left blank)" in body


def test_off_without_settings(monkeypatch):
    setup(monkeypatch, password="")
    assert notify.application_sent(JOB, []).startswith("off")


def test_sends(monkeypatch):
    setup(monkeypatch)
    sent = []
    monkeypatch.setattr(notify, "send", sent.append)
    assert notify.application_sent(JOB, []) == "sent to track@gmail.com"
    assert sent[0]["Subject"].startswith("[Wellfound] Applied:")


def test_failure_is_reported_not_raised(monkeypatch):
    setup(monkeypatch)
    def boom(_msg):
        raise smtplib.SMTPAuthenticationError(535, b"bad")
    monkeypatch.setattr(notify, "send", boom)
    assert "app password" in notify.application_sent(JOB, [])


def test_indeed_subject(monkeypatch):
    setup(monkeypatch)
    job = {**JOB, "site": "Indeed", "applied_url": "https://myjobs.indeed.com/applied"}
    msg = notify.build_message(job, [])
    assert msg["Subject"] == "[Indeed] Applied: AI Engineer at Acme"
    assert "on Indeed." in msg.get_content() and "myjobs.indeed.com/applied" in msg.get_content()
