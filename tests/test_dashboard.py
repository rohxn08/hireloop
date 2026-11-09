"""Dashboard stats come straight from the local log."""

from hireloop import config, dashboard, store


def test_stats(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    assert dashboard.stats()["totals"]["submitted"] == 0        # no log yet: still works

    store.record("1-a", "submitted", title="AI Engineer", company="Acme")
    store.record("2-b", "submitted", title="ML Engineer", company="Acme")
    store.record("3-c", "skipped", title="SWE", company="Beta", notes="Needs 5+ years")
    store.record("4-d", "drafted", title="CV Engineer", company="Gamma")
    s = dashboard.stats()
    assert s["totals"] == {"submitted": 2, "this_week": 2, "drafted": 1, "skipped": 1, "held": 0}
    assert s["per_day"][-1] == {"day": s["today"], "count": 2} and len(s["per_day"]) == 30
    assert s["top_companies"] == [("Acme", 2)]
    assert s["skip_reasons"] == [("Needs 5+ years", 1)]
    assert s["cap"]["submitted_today"] == 2
    assert all(r["url"].startswith("https://wellfound.com/jobs/") for r in s["rows"])
