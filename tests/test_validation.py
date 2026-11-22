"""Validation against known real events, on the stored data (not hand-made).

These check that the model "sees" events we know caused disruption.
Skipped if the database hasn't been built yet (run the ingest first).
"""

import pytest

from weather_risk.config import DB_PATH
from weather_risk.db import connect
from weather_risk.scoring import is_ice_day, load_scoring_config

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="run the ingest first")


def ice_params() -> dict:
    sigs = load_scoring_config()["hazards"]["winter"]["components"][0]["signals"]
    return next(s for s in sigs if s["id"] == "ice_days")


def ice_days_in(hub_id: str, start: str, end: str) -> list[str]:
    conn = connect()
    rows = conn.execute("SELECT * FROM weather_daily WHERE hub_id = ? AND date BETWEEN ? AND ?",
                        (hub_id, start, end)).fetchall()
    conn.close()
    return [r["date"] for r in rows if is_ice_day(dict(r), ice_params())]


@pytest.mark.parametrize("start,end,label", [
    ("2022-02-01", "2022-02-05", "DFW ice/sleet storm, Feb 2022"),
    ("2023-01-29", "2023-02-03", "DFW ice storm, Jan-Feb 2023"),
])
def test_dfw_ice_events_are_detected(start, end, label):
    assert ice_days_in("dallas", start, end), f"ice rule missed: {label}"


@pytest.mark.xfail(strict=True, reason="F6: Feb 2021 shows as snow/cold in Open-Meteo, not ice. "
                                       "Known limitation; captured by the FEMA component instead.")
def test_dfw_feb_2021_ice_detected():
    assert ice_days_in("dallas", "2021-02-10", "2021-02-20")
