"""Which scoring window to use right now (auto window, user decision).

Rule: the window ends at the last complete calendar year, but it only moves forward once EVERY
source has that full year stored. Until then it stays where it is and the reasons are reported.

  weather (Open-Meteo)   every hub has a row for Dec 31 of that year
  hail (NOAA SPC)        a successful ingest run for that year's file
  tracks (NHC HURDAT2)   the stored file contains storms from that year
  FEMA                   pulled daily and declarations arrive continuously, so it's never a blocker;
                         late declarations show up later as "new FEMA event" alerts
"""

import copy
import sqlite3
from datetime import date


def source_gaps(conn: sqlite3.Connection, year: int, n_hubs: int) -> list[str]:
    """Reasons why `year` is not yet complete in every source (empty list = complete)."""
    gaps = []
    hubs_with_dec31 = conn.execute("SELECT COUNT(DISTINCT hub_id) FROM weather_daily WHERE date = ?",
                                   (f"{year}-12-31",)).fetchone()[0]
    if hubs_with_dec31 < n_hubs:
        last = conn.execute("SELECT MAX(date) FROM weather_daily").fetchone()[0]
        gaps.append(f"weather: data only through {last}")
    hail_ok = conn.execute("SELECT COUNT(*) FROM ingest_runs WHERE source = 'spc-hail-reports' AND status = 'ok' "
                           "AND start_date = ?", (f"{year}-01-01",)).fetchone()[0]
    if not hail_ok:
        gaps.append(f"hail: SPC {year} file not ingested yet")
    track_max = conn.execute("SELECT MAX(year) FROM storm_track_points").fetchone()[0] or 0
    if track_max < year:
        gaps.append(f"tracks: HURDAT2 stored only through {track_max}")
    return gaps


def catch_up(conn: sqlite3.Connection, year: int, n_hubs: int) -> list[str]:
    """Try to fetch ONLY what's missing for `year`, so the window can move forward.

    Reuses the normal ingest code (same throttling, retries and upserts). Returns a log of
    what was attempted and what's still missing afterwards.
      weather: fetch from the day after the last stored day up to today-2 (all hubs)
      hail:    fetch that year's SPC file (it appears months after the year ends)
      tracks:  the newest HURDAT2 release, if it covers more years (ingest/hurdat2.py update())
    """
    from datetime import timedelta
    from weather_risk.ingest import open_meteo, spc_hail
    log = []
    gaps = source_gaps(conn, year, n_hubs)
    if any(g.startswith("weather") for g in gaps):
        last = conn.execute("SELECT MIN(last) FROM (SELECT MAX(date) AS last FROM weather_daily GROUP BY hub_id)").fetchone()[0]
        start, end = date.fromisoformat(last) + timedelta(days=1), open_meteo.default_end_date()
        if start <= end:
            open_meteo.run(start, end)
            log.append(f"weather: fetched {start}..{end}")
    if any(g.startswith("hail") for g in gaps):
        spc_hail.run([year])
        log.append(f"hail: tried SPC {year} file")
    if any(g.startswith("tracks") for g in gaps):
        from weather_risk.ingest import hurdat2
        log.append(hurdat2.update(conn))     # newest release; validated; whole-table swap in one transaction
    still = source_gaps(conn, year, n_hubs)
    log.append(f"{year} complete in all sources" if not still else f"{year} still missing: {'; '.join(still)}")
    return log


def shift_window(cfg: dict, end_year: int) -> dict:
    """Copy of cfg with the main window ending in end_year (same length) and track windows shifted alike."""
    cfg = copy.deepcopy(cfg)
    start, end = int(cfg["window"]["start"][:4]), int(cfg["window"]["end"][:4])
    offset = end_year - end
    cfg["window"]["start"] = f"{start + offset}-01-01"
    cfg["window"]["end"] = f"{end_year}-12-31"
    for hz in cfg["hazards"].values():
        for c in hz["components"]:
            if c["type"] == "storm_tracks":
                c["window_start_year"] += offset
                c["window_end_year"] += offset
    return cfg


def resolve_window(conn: sqlite3.Connection, cfg: dict, n_hubs: int, today: date | None = None) -> tuple[dict, str]:
    """Returns (effective cfg, note explaining the window choice)."""
    configured_end = int(cfg["window"]["end"][:4])
    if not cfg["window"].get("auto"):
        return cfg, f"window pinned in config: {cfg['window']['start'][:4]}-{configured_end}"
    target = (today or date.today()).year - 1            # last complete calendar year
    for year in range(target, configured_end - 1, -1):   # newest complete year, never older than configured
        gaps = source_gaps(conn, year, n_hubs)
        if not gaps:
            eff = shift_window(cfg, year)
            note = f"window {eff['window']['start'][:4]}-{year}"
            if year < target:
                note += f" (kept: {target} not complete in all sources: {'; '.join(source_gaps(conn, target, n_hubs))})"
            return eff, note
    return cfg, (f"window {cfg['window']['start'][:4]}-{configured_end} (configured end year itself is incomplete: "
                 f"{'; '.join(source_gaps(conn, configured_end, n_hubs))})")
