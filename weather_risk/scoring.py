"""Deterministic scoring engine: stored data + config/scoring.toml -> scores with full breakdown.

Run:  .venv\\Scripts\\python.exe -m weather_risk.scoring      (prints the ranking)

Design:
- The scoring functions are *pure*: plain lists of dicts in, results out, no DB
  or network. That makes them easy to unit-test with hand-made data.
- Every score carries its evidence (raw values, bounds, FEMA event titles),
  so the agent can explain "why" using numbers from code, never from the LLM.
- Components are chosen by their `type`, so a new data source (e.g. HURDAT2
  "storm_tracks") is one new function in COMPONENT_SCORERS plus config.
"""

import math
import sqlite3
import tomllib
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from weather_risk.config import CONFIG_DIR, Hub, load_hubs

DAYS_PER_YEAR = 365.25


# ------------------------------------------------------------------ result models
# Pydantic models: typed, and they turn into JSON for the API/agent for free.

class SignalResult(BaseModel):
    id: str
    label: str
    value: float          # days per year (weather) or events per 10 years (FEMA)
    unit: str
    bound: float          # the value that scores 100
    score: float          # 0-100


class FemaEvent(BaseModel):
    incident_id: str
    incident_type: str
    title: str
    begin_date: str
    declarations: list[str]   # e.g. ['EM-3554-TX', 'DR-4586-TX']


class StormPass(BaseModel):
    storm_id: str
    name: str
    year: int
    closest_km: float


class ComponentResult(BaseModel):
    type: str
    weight: float
    score: float
    signals: list[SignalResult]
    events: list[FemaEvent] = []   # only for fema_events
    storms: list[StormPass] = []   # only for storm_tracks


class HazardResult(BaseModel):
    hazard: str
    label: str
    score: float
    components: list[ComponentResult]
    notes: list[str] = []


class HubScore(BaseModel):
    hub_id: str
    hub_name: str
    region: str
    overall: float
    hazards: dict[str, HazardResult]
    window: str


# ------------------------------------------------------------------ helpers

def scale(value: float, bound: float) -> float:
    """Absolute normalization (A13): 0 -> 0, bound or more -> 100, linear between."""
    return round(min(value / bound, 1.0) * 100, 1)


def _ge(field):  # value present and >= threshold
    return lambda day, s: day[field] is not None and day[field] >= s["threshold"]


def _le(field):
    return lambda day, s: day[field] is not None and day[field] <= s["threshold"]


def is_ice_day(day: dict, s: dict) -> bool:
    """Likely freezing rain: wet, cold all day, and mostly NOT snow.

    Snow is in cm and precipitation in mm of water. If all of it fell as snow,
    snowfall_cm ~= snow_cm_per_mm * precipitation_mm (0.7, measured in our
    data, A13). "Mostly not snow" = snowfall below max_snow_fraction of that.
    """
    p, tmax, snow = day["precipitation_mm"], day["temp_max_c"], day["snowfall_cm"]
    if p is None or tmax is None or snow is None:
        return False
    all_snow_cm = s["snow_cm_per_mm"] * p
    return p >= s["min_precip_mm"] and tmax <= s["max_temp_max_c"] and snow < s["max_snow_fraction"] * all_snow_cm


# Rule name in config -> function(day, signal_config) -> bool
DAY_RULES: dict[str, Callable[[dict, dict], bool]] = {
    "snowfall_at_least": _ge("snowfall_cm"),
    "precipitation_at_least": _ge("precipitation_mm"),
    "gust_at_least": _ge("wind_gust_max_kmh"),
    "temp_min_at_most": _le("temp_min_c"),
    "temp_max_at_least": _ge("temp_max_c"),
    "ice_day": is_ice_day,
}


# ------------------------------------------------------------------ components (pure)

def make_signal(id: str, label: str, raw: float, unit: str, bound: float) -> SignalResult:
    """One rounding rule everywhere: the value shown is rounded to 1 decimal; the score is computed
    from the unrounded value (so rounding never changes a score). Before this, values had 2 decimals
    ("40.39") next to 1-decimal scores, which looked inconsistent in answers."""
    return SignalResult(id=id, label=label, value=round(raw, 1), unit=unit, bound=bound, score=scale(raw, bound))


def score_weather_days(comp: dict, ctx: dict) -> ComponentResult:
    days = ctx["weather_days"]
    years = len(days) / DAYS_PER_YEAR
    signals = []
    for s in comp["signals"]:
        rule = DAY_RULES[s["rule"]]
        per_year = sum(1 for d in days if rule(d, s)) / years if years else 0.0
        signals.append(make_signal(s["id"], s["label"], per_year, "days/yr", s["bound"]))
    avg = round(sum(x.score for x in signals) / len(signals), 1)
    return ComponentResult(type="weather_days", weight=comp["weight"], score=avg, signals=signals)


def counts_under_rule(decl: dict, rule: str) -> bool:
    if rule == "A":
        return True
    if rule == "B":
        return decl["declaration_type"] == "DR"
    if rule == "D":
        return decl["declaration_type"] == "DR" and bool(decl["ih_declared"])
    raise ValueError(f"Unknown FEMA rule {rule!r}")


def score_fema_events(comp: dict, ctx: dict) -> ComponentResult:
    rule, years = ctx["fema_rule"], ctx["window_years"]
    # An event counts if ANY of its declarations passes the rule (e.g. the DR of an EM+DR pair).
    by_incident: dict[str, list[dict]] = {}
    for d in ctx["declarations"]:
        if d["incident_type"] in comp["incident_types"]:
            by_incident.setdefault(d["incident_id"], []).append(d)
    events = []
    for inc_id, decls in sorted(by_incident.items()):
        if any(counts_under_rule(d, rule) for d in decls):
            first = min(decls, key=lambda d: d["incident_begin_date"])
            events.append(FemaEvent(incident_id=inc_id, incident_type=first["incident_type"], title=first["title"],
                                    begin_date=first["incident_begin_date"],
                                    declarations=sorted(d["declaration_id"] for d in decls)))
    per_10y = len(events) / years * 10 if years else 0.0
    signal = make_signal("fema_events", f"FEMA events (rule {rule}): {', '.join(comp['incident_types'])}",
                         per_10y, "events/10yr", comp["bound"])
    return ComponentResult(type="fema_events", weight=comp["weight"], score=signal.score, signals=[signal], events=events)


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle (haversine) distance."""
    p = math.pi / 180
    h = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def score_hail_days(comp: dict, ctx: dict) -> ComponentResult:
    """Days per year with at least one hail report >= size within radius of the hub point."""
    hub, years = ctx["hub"], ctx["window_years"]
    signals = []
    for s in comp["signals"]:
        days = {r["date"] for r in ctx["hail_reports"]
                if r["size_in"] >= s["min_size_in"]
                and distance_km(hub.lat, hub.lon, r["lat"], r["lon"]) <= s["radius_km"]}
        per_year = len(days) / years if years else 0.0
        signals.append(make_signal(s["id"], s["label"], per_year, "days/yr", s["bound"]))
    avg = round(sum(x.score for x in signals) / len(signals), 1)
    return ComponentResult(type="hail_days", weight=comp["weight"], score=avg, signals=signals)


def closest_approach_km(hub: Hub, pts: list[tuple], statuses: set[str]) -> float | None:
    """Closest distance from the hub to the storm track while the storm had one of `statuses`.

    Positions are 6 hours apart (~100-150 km), so we check 10 interpolated points along each
    segment, not only the reported positions. A segment counts if either end has a qualifying status.
    """
    best = None
    for (s1, la1, lo1), (s2, la2, lo2) in zip(pts, pts[1:]):
        if s1 not in statuses and s2 not in statuses:
            continue
        if min(abs(la1 - hub.lat), abs(la2 - hub.lat)) > 6 or min(abs(lo1 - hub.lon), abs(lo2 - hub.lon)) > 8:
            continue                                     # far away: skip the distance math
        for k in range(11):
            t = k / 10
            d = distance_km(hub.lat, hub.lon, la1 + t * (la2 - la1), lo1 + t * (lo2 - lo1))
            best = d if best is None or d < best else best
    if len(pts) == 1 and pts[0][0] in statuses:
        best = distance_km(hub.lat, hub.lon, pts[0][1], pts[0][2])
    return best


def score_storm_tracks(comp: dict, ctx: dict) -> ComponentResult:
    """Tropical storms/hurricanes passing within radius_km of the hub, per 10 years (NOAA HURDAT2).

    Uses its OWN window (e.g. 1991-2025): these events are rare, so 10 years gives counts of 0-3.
    """
    hub, statuses = ctx["hub"], set(comp["statuses"])
    y0, y1 = comp["window_start_year"], comp["window_end_year"]
    passes = []
    for sid, st in ctx["storms"].items():
        if not (y0 <= st["year"] <= y1):
            continue
        d = closest_approach_km(hub, st["pts"], statuses)
        if d is not None and d <= comp["radius_km"]:
            passes.append(StormPass(storm_id=sid, name=st["name"], year=st["year"], closest_km=round(d)))
    per_10y = len(passes) / ((y1 - y0 + 1) / 10)
    signal = make_signal("storm_passes",
                         f"Tropical storms/hurricanes passing within {comp['radius_km']} km ({y0}-{y1}, NOAA HURDAT2)",
                         per_10y, "storms/10yr", comp["bound"])
    return ComponentResult(type="storm_tracks", weight=comp["weight"], score=signal.score, signals=[signal],
                           storms=sorted(passes, key=lambda p: (p.year, p.name)))


COMPONENT_SCORERS = {
    "weather_days": score_weather_days,
    "fema_events": score_fema_events,
    "hail_days": score_hail_days,
    "storm_tracks": score_storm_tracks,
}


def combine_hazards(hazard_scores: dict[str, float], cfg: dict) -> float:
    """Overall score from hazard scores (0-100 each).

    noisy_or: treat each hazard score as a chance-like 0-1 value; overall is the
    chance that at least one hazard "hits": 1 - prod(1 - p). A low hazard can't
    offset a high one, and one hazard at 100 makes the overall 100.
    weighted_average: the original model, kept for comparison.
    """
    method = cfg["overall"]["method"]
    if method == "noisy_or":
        prod = 1.0
        for s in hazard_scores.values():
            prod *= 1 - s / 100
        return round(100 * (1 - prod), 1)
    if method == "weighted_average":
        return round(sum(cfg["hazard_weights"][h] * s for h, s in hazard_scores.items()), 1)
    raise ValueError(f"unknown overall method {method!r}")


def score_hub(hub: Hub, weather_days: list[dict], declarations: list[dict], cfg: dict,
              hail_reports: list[dict] = (), storms: dict | None = None) -> HubScore:
    """Pure: everything needed is passed in."""
    ctx = {"hub": hub, "weather_days": weather_days, "declarations": declarations, "hail_reports": hail_reports,
           "storms": storms or {},
           "fema_rule": cfg["fema"]["rule"], "window_years": window_years(cfg)}
    hazards = {}
    for name, hz in cfg["hazards"].items():
        comps = [COMPONENT_SCORERS[c["type"]](c, ctx) for c in hz["components"]]
        score = round(sum(c.weight * c.score for c in comps), 1)
        notes = [hz["note"]] if "note" in hz else []   # caveat the agent must mention
        hazards[name] = HazardResult(hazard=name, label=hz["label"], score=score, components=comps, notes=notes)
    overall = combine_hazards({h: r.score for h, r in hazards.items()}, cfg)
    w = cfg["window"]
    return HubScore(hub_id=hub.id, hub_name=hub.name, region=hub.region, overall=overall,
                    hazards=hazards, window=f"{w['start']}..{w['end']}")


# ------------------------------------------------------------------ config + data loading

def window_years(cfg: dict) -> float:
    from datetime import date
    w = cfg["window"]
    return ((date.fromisoformat(w["end"]) - date.fromisoformat(w["start"])).days + 1) / DAYS_PER_YEAR


def load_scoring_config(path: Path = CONFIG_DIR / "scoring.toml") -> dict:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    # Fail fast on config mistakes: weights must sum to 1, types must exist.
    if cfg["overall"]["method"] not in ("noisy_or", "weighted_average"):
        raise ValueError("overall.method must be 'noisy_or' or 'weighted_average'")
    if abs(sum(cfg["hazard_weights"].values()) - 1) > 1e-9:
        raise ValueError("hazard_weights must sum to 1")
    if set(cfg["hazard_weights"]) != set(cfg["hazards"]):
        raise ValueError("hazard_weights and hazards must list the same hazards")
    for name, hz in cfg["hazards"].items():
        if abs(sum(c["weight"] for c in hz["components"]) - 1) > 1e-9:
            raise ValueError(f"{name}: component weights must sum to 1")
        for c in hz["components"]:
            if c["type"] not in COMPONENT_SCORERS:
                raise ValueError(f"{name}: unknown component type {c['type']!r}")
            for s in c.get("signals", []) if c["type"] == "weather_days" else []:
                if s["rule"] not in DAY_RULES:
                    raise ValueError(f"{name}: unknown rule {s['rule']!r}")
    return cfg


def load_hub_data(conn: sqlite3.Connection, hub: Hub, cfg: dict) -> tuple[list[dict], list[dict], list[dict]]:
    w = (cfg["window"]["start"], cfg["window"]["end"])
    days = [dict(r) for r in conn.execute(
        "SELECT * FROM weather_daily WHERE hub_id = ? AND date BETWEEN ? AND ?", (hub.id, *w))]
    decls = [dict(r) for r in conn.execute(
        "SELECT * FROM fema_declarations WHERE hub_id = ? AND incident_begin_date BETWEEN ? AND ?", (hub.id, *w))]
    # Rough box (~1 degree, > any radius we use) in SQL; exact distance check in score_hail_days.
    hail = [dict(r) for r in conn.execute(
        "SELECT date, size_in, lat, lon FROM hail_reports WHERE date BETWEEN ? AND ? "
        "AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
        (*w, hub.lat - 1, hub.lat + 1, hub.lon - 1.5, hub.lon + 1.5))]
    return days, decls, hail


def load_storms(conn: sqlite3.Connection) -> dict:
    """{storm_id: {name, year, pts: [(status, lat, lon), ...]}} in track order."""
    storms: dict = {}
    for r in conn.execute("SELECT storm_id, name, year, status, lat, lon FROM storm_track_points "
                          "ORDER BY storm_id, seq"):
        st = storms.setdefault(r["storm_id"], {"name": r["name"], "year": r["year"], "pts": []})
        st["pts"].append((r["status"], r["lat"], r["lon"]))
    return storms


def score_all(conn: sqlite3.Connection, cfg: dict | None = None, hubs: list[Hub] | None = None) -> list[HubScore]:
    """All hubs, ranked by overall score (highest first)."""
    cfg = cfg or load_scoring_config()
    storms = load_storms(conn)        # all tracks once; each hub picks the storms near it
    results = []
    for h in hubs or load_hubs():
        days, decls, hail = load_hub_data(conn, h, cfg)
        results.append(score_hub(h, days, decls, cfg, hail, storms))
    return sorted(results, key=lambda r: r.overall, reverse=True)


def main() -> None:
    from weather_risk.db import connect
    conn = connect()
    ranked = score_all(conn)
    names = list(ranked[0].hazards)
    print(f"{'#':<3}{'hub':<14}{'region':<9}{'overall':>8}" + "".join(f"{n:>14}" for n in names))
    for i, r in enumerate(ranked, 1):
        print(f"{i:<3}{r.hub_name:<14}{r.region:<9}{r.overall:>8}" + "".join(f"{r.hazards[n].score:>14}" for n in names))
    conn.close()


if __name__ == "__main__":
    main()
