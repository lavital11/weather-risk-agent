"""Unit tests for the scoring engine. Hand-made data only: no DB, no network.

Run:  .venv\\Scripts\\python.exe -m pytest -q
"""

import copy
from datetime import date, timedelta

import pytest

from weather_risk.config import Hub
from weather_risk.scoring import (
    combine_hazards, is_ice_day, load_scoring_config, scale, score_fema_events, score_hail_days, score_hub, score_storm_tracks, score_weather_days,
)

HUB = Hub(id="test", name="Test", airport="TST", lat=0, lon=0, timezone="UTC", state="TX",
          state_fips="48", county_fips="001", county="Test", region="South")
ICE = {"min_precip_mm": 2.0, "max_temp_max_c": 1.0, "max_snow_fraction": 0.5, "snow_cm_per_mm": 0.7}


def day(snow=0.0, precip=0.0, gust=10.0, tmin=5.0, tmax=15.0, d="2020-01-01"):
    return {"date": d, "snowfall_cm": snow, "precipitation_mm": precip,
            "wind_gust_max_kmh": gust, "temp_min_c": tmin, "temp_max_c": tmax}


def year_of_days(n_special: int, special: dict) -> list[dict]:
    """365.25 days (≈ 1 year): n_special days with `special` values, the rest calm."""
    start = date(2020, 1, 1)
    out = [day(d=(start + timedelta(i)).isoformat()) for i in range(1461)]  # 4 years incl. one leap day
    for i in range(n_special * 4):                                          # n_special per year
        out[i].update(special)
    return out


def decl(decl_id, incident_id, itype="Hurricane", dtype="DR", ih=1, begin="2017-08-25"):
    return {"declaration_id": decl_id, "incident_id": incident_id, "incident_type": itype,
            "declaration_type": dtype, "ih_declared": ih, "title": f"T {incident_id}",
            "incident_begin_date": begin}


# --------------------------------------------------------------- normalization

@pytest.mark.parametrize("value,bound,expected", [(0, 40, 0.0), (20, 40, 50.0), (40, 40, 100.0), (80, 40, 100.0)])
def test_scale_is_linear_and_capped(value, bound, expected):
    assert scale(value, bound) == expected


# --------------------------------------------------------------- ice rule

def test_ice_day_freezing_rain():
    assert is_ice_day(day(precip=10, snow=0.5, tmax=0), ICE)        # 0.5 cm << 0.35 * 10 = 3.5


def test_ice_day_rejects_snow_day():
    assert not is_ice_day(day(precip=10, snow=7, tmax=-5), ICE)     # all snow: 7 cm ≈ 0.7 * 10


def test_ice_day_rejects_warm_rain_and_missing_values():
    assert not is_ice_day(day(precip=10, snow=0, tmax=8), ICE)      # too warm
    assert not is_ice_day(day(precip=1, snow=0, tmax=0), ICE)       # too little precipitation
    assert not is_ice_day(day(precip=None, snow=0, tmax=0), ICE)    # NULL is not evidence


# --------------------------------------------------------------- weather component

def test_weather_component_counts_days_per_year():
    comp = {"weight": 0.7, "signals": [
        {"id": "snow_days", "rule": "snowfall_at_least", "threshold": 0.25, "bound": 40, "label": "snow"}]}
    result = score_weather_days(comp, {"weather_days": year_of_days(10, {"snowfall_cm": 1.0})})
    sig = result.signals[0]
    assert sig.value == pytest.approx(10, abs=0.01)   # 10 snow days per year
    assert sig.score == 25.0                          # 10 / 40 * 100


def test_threshold_is_inclusive():
    comp = {"weight": 1.0, "signals": [
        {"id": "s", "rule": "snowfall_at_least", "threshold": 0.25, "bound": 40, "label": "s"}]}
    result = score_weather_days(comp, {"weather_days": year_of_days(4, {"snowfall_cm": 0.25})})
    assert result.signals[0].value == pytest.approx(4, abs=0.01)


# --------------------------------------------------------------- FEMA component (rule D etc.)

FEMA_COMP = {"weight": 1.0, "bound": 2, "incident_types": ["Hurricane"]}


def fema_ctx(decls, rule="D"):
    return {"declarations": decls, "fema_rule": rule, "window_years": 10.0}


def test_em_dr_pair_counts_once():
    decls = [decl("EM-1", "inc1", dtype="EM", ih=0), decl("DR-1", "inc1", dtype="DR", ih=1)]
    r = score_fema_events(FEMA_COMP, fema_ctx(decls))
    assert len(r.events) == 1
    assert r.events[0].declarations == ["DR-1", "EM-1"]


def test_rule_d_excludes_em_only_and_dr_without_household_aid():
    decls = [decl("EM-2", "inc2", dtype="EM", ih=0),     # precautionary
             decl("DR-3", "inc3", dtype="DR", ih=0)]     # e.g. Dallas Harvey: support work, no IH
    assert score_fema_events(FEMA_COMP, fema_ctx(decls, "D")).events == []
    assert len(score_fema_events(FEMA_COMP, fema_ctx(decls, "B")).events) == 1   # B keeps the DR
    assert len(score_fema_events(FEMA_COMP, fema_ctx(decls, "A")).events) == 2   # A keeps both


def test_fema_filters_incident_type_and_scales_per_10_years():
    decls = [decl("DR-4", "inc4"), decl("DR-5", "inc5", itype="Biological")]   # COVID ignored
    r = score_fema_events(FEMA_COMP, fema_ctx(decls))
    assert r.signals[0].value == 1.0      # 1 event in 10 years
    assert r.score == 50.0                # 1 / 2 * 100


# --------------------------------------------------------------- whole hub + config

def test_real_config_loads_and_validates():
    cfg = load_scoring_config()
    assert cfg["fema"]["rule"] == "D"
    assert set(cfg["hazards"]) == {"winter", "flood", "severe_storm", "hurricane", "heat"}


def test_bad_weights_are_rejected(tmp_path):
    bad = (load_scoring_config.__defaults__[0]).read_text(encoding="utf-8").replace("winter       = 0.2", "winter       = 0.5")
    p = tmp_path / "scoring.toml"
    p.write_text(bad, encoding="utf-8")
    with pytest.raises(ValueError, match="sum to 1"):
        load_scoring_config(p)


def test_hub_score_is_weighted_sum_and_hurricane_has_small_sample_note():
    cfg = load_scoring_config()
    days = year_of_days(20, {"snowfall_cm": 1.0})          # 20 snow days/yr -> snow_days signal 50
    r = score_hub(HUB, days, [], cfg)
    winter = r.hazards["winter"]
    weather = winter.components[0]
    assert weather.signals[0].score == 50.0
    assert winter.score == pytest.approx(round(0.7 * weather.score + 0.3 * 0.0, 1))
    prod = 1.0
    for h in r.hazards.values():
        prod *= 1 - h.score / 100
    assert r.overall == pytest.approx(round(100 * (1 - prod), 1))     # noisy-OR is the configured method
    assert r.hazards["hurricane"].notes                     # hazard note (A18) the agent must mention


@pytest.mark.parametrize("scores,expected", [
    ({"a": 0, "b": 0}, 0.0),
    ({"a": 50, "b": 50}, 75.0),        # 1 - 0.5 * 0.5
    ({"a": 40, "b": 0}, 40.0),         # a zero hazard doesn't pull the score down (unlike an average)
    ({"a": 100, "b": 10}, 100.0),      # saturation: one hazard at 100 -> overall 100
])
def test_noisy_or(scores, expected):
    assert combine_hazards(scores, {"overall": {"method": "noisy_or"}}) == expected


def test_weighted_average_still_available():
    cfg = {"overall": {"method": "weighted_average"}, "hazard_weights": {"a": 0.5, "b": 0.5}}
    assert combine_hazards({"a": 40, "b": 0}, cfg) == 20.0


def test_hail_counts_days_not_reports_and_respects_radius():
    comp = {"weight": 1.0, "signals": [{"id": "h", "min_size_in": 1.0, "radius_km": 50, "bound": 10, "label": "h"}]}
    near, far = (0.1, 0.1), (2.0, 2.0)          # ~16 km and ~314 km from HUB at (0, 0)
    reports = [
        {"date": "2020-05-01", "size_in": 1.5, "lat": near[0], "lon": near[1]},
        {"date": "2020-05-01", "size_in": 2.0, "lat": near[0], "lon": near[1]},   # same day -> still 1 day
        {"date": "2020-05-02", "size_in": 0.75, "lat": near[0], "lon": near[1]},  # too small
        {"date": "2020-05-03", "size_in": 3.0, "lat": far[0], "lon": far[1]},     # too far
    ]
    r = score_hail_days(comp, {"hub": HUB, "hail_reports": reports, "window_years": 10.0})
    assert r.signals[0].value == 0.1             # 1 day in 10 years
    assert r.score == 1.0                        # 0.1 / 10 * 100


TRACK_COMP = {"weight": 1.0, "radius_km": 100, "statuses": ["TS", "HU"],
              "window_start_year": 1991, "window_end_year": 2000, "bound": 8}


def test_storm_track_uses_segment_not_just_points_and_filters_status_and_window():
    storms = {
        # Both reported positions are ~222 km from HUB (0, 0), but the path between them passes ~56 km away.
        "near_ts": {"name": "NEAR", "year": 1995, "pts": [("TS", -2.0, 0.5), ("TS", 2.0, 0.5)]},
        "near_td": {"name": "WEAK", "year": 1995, "pts": [("TD", -2.0, 0.5), ("TD", 2.0, 0.5)]},     # too weak
        "old":     {"name": "OLD",  "year": 1980, "pts": [("HU", -2.0, 0.5), ("HU", 2.0, 0.5)]},     # outside window
        "far":     {"name": "FAR",  "year": 1995, "pts": [("HU", 10.0, 10.0), ("HU", 11.0, 11.0)]},  # too far
    }
    r = score_storm_tracks(TRACK_COMP, {"hub": HUB, "storms": storms})
    assert [s.name for s in r.storms] == ["NEAR"]
    assert 50 <= r.storms[0].closest_km <= 60
    assert r.signals[0].value == 1.0          # 1 storm in 10 years (1991-2000)
    assert r.score == 12.5                    # 1 / 8 * 100


def test_bounds_are_fixed_so_scores_dont_depend_on_other_hubs():
    # score_hub only sees one hub's data: same input -> same output, whatever else exists.
    cfg = load_scoring_config()
    days = year_of_days(5, {"precipitation_mm": 60.0})
    assert score_hub(HUB, days, [], cfg) == score_hub(HUB, copy.deepcopy(days), [], cfg)
