"""Alert logic (pure compare) and the auto-window rule (on the stored data)."""

from datetime import date

import pytest

from weather_risk.alerts import compare
from weather_risk.config import DB_PATH
from weather_risk.db import connect
from weather_risk.scoring import load_scoring_config
from weather_risk.window import resolve_window

TH = {"rank_change": True, "overall_change": 2.0, "hazard_change": 10.0, "fema_events": True}


def hub(name, overall, rank, hazards=None, fema=None):
    return {"name": name, "overall": overall, "rank": rank, "hazards": hazards or {"flood": 10.0},
            "fema_events": fema or {}}


def test_small_drift_does_not_alert():
    old = {"a": hub("A", 50.0, 1), "b": hub("B", 40.0, 2)}
    new = {"a": hub("A", 51.5, 1), "b": hub("B", 38.1, 2)}          # 1.5 and 1.9 < 2.0
    assert compare(old, new, TH, "x", "x") == {}


def test_each_threshold_triggers_and_reasons_are_combined():
    old = {"a": hub("A", 60.0, 1, {"flood": 50.0}, {"i1": "STORM 2015"}), "b": hub("B", 59.0, 2)}
    new = {"a": hub("A", 58.5, 2, {"flood": 30.0}, {}), "b": hub("B", 59.0, 1)}
    out = compare(old, new, TH, "x", "x")
    reasons = " | ".join(out["a"])
    assert "rank #1 -> #2" in reasons and "effectively tied with B" in reasons   # 58.5 vs 59.0
    assert "flood score 50.0 -> 30.0 (-20.0)" in reasons
    assert "FEMA event left the window: STORM 2015" in reasons
    assert "overall" not in reasons                                             # -1.5 < 2.0
    assert "rank #2 -> #1" in out["b"][0]


def test_new_fema_event_and_config_change_are_reported():
    old = {"a": hub("A", 50.0, 1)}
    new = {"a": hub("A", 50.0, 1, fema={"i9": "NEW FLOOD"})}
    out = compare(old, new, TH, "cfg1", "cfg2")
    assert out["a"][0] == "new FEMA event counts: NEW FLOOD (i9)"
    assert "scoring config changed (cfg1 -> cfg2)" in out["a"][1]


@pytest.mark.skipif(not DB_PATH.exists(), reason="run the ingest first")
def test_catch_up_fetches_only_what_is_missing(monkeypatch):
    # No network and no DB writes: the fetchers are replaced with recorders.
    from weather_risk import window
    from weather_risk.ingest import open_meteo, spc_hail
    calls = []
    monkeypatch.setattr(open_meteo, "default_end_date", lambda: date(2027, 1, 1))   # pretend it's January 2027
    monkeypatch.setattr(open_meteo, "run", lambda s, e: calls.append(("weather", s, e)))
    monkeypatch.setattr(spc_hail, "run", lambda years: calls.append(("hail", list(years))))
    log = window.catch_up(connect(), 2026, 10)
    last = connect().execute("SELECT MIN(m) FROM (SELECT MAX(date) m FROM weather_daily GROUP BY hub_id)").fetchone()[0]
    assert calls[0] == ("weather", date.fromisoformat(last) + __import__("datetime").timedelta(days=1), date(2027, 1, 1))
    assert calls[1] == ("hail", [2026])                    # only the missing year's file
    assert any("HURDAT2" in line for line in log)          # tracks: logged as a manual step
    assert "2026 still missing" in log[-1]                 # fetchers were fakes, so still missing


@pytest.mark.skipif(not DB_PATH.exists(), reason="run the ingest first")
def test_auto_window_moves_only_when_every_source_has_the_year():
    conn, cfg = connect(), load_scoring_config()
    eff, note = resolve_window(conn, cfg, 10, today=date(2026, 11, 22))
    assert eff["window"]["end"] == "2025-12-31"                                 # 2025 complete everywhere
    eff, note = resolve_window(conn, cfg, 10, today=date(2027, 1, 3))           # 2026 not complete yet
    assert eff["window"]["end"] == "2025-12-31"
    assert "kept: 2026 not complete" in note and "hail" in note and "tracks" in note
