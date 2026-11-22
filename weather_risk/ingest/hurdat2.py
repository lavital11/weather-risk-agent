"""Fetch NOAA NHC HURDAT2 (Atlantic hurricane best tracks) into storm_track_points.

Run:  .venv\\Scripts\\python.exe -m weather_risk.ingest.hurdat2

Source: https://www.nhc.noaa.gov/data/hurdat/  (one text file, 1851-2025, ~7 MB).
Format (checked against NHC's hurdat2-format-atlantic.pdf):
  header line:  AL092017, HARVEY, 74,          -> storm id (basin + number + year), name, number of points
  data line:    20170825, 1800, L, HU, 28.0N, 96.9W, 115, 938, ...
                date, time (UTC), record id (L = landfall), status, lat, lon, max wind (kt), ...
Status: TD < 34 kt, TS 34-63 kt, HU >= 64 kt, EX extratropical, SD/SS subtropical, LO/WV/DB other.
"""

from datetime import datetime, timezone

import httpx

from weather_risk.db import connect

URL = "https://www.nhc.noaa.gov/data/hurdat/hurdat2-1851-2025-092326.txt"
SOURCE = "nhc-hurdat2-atlantic"
MIN_YEAR = 1990  # scoring uses 1991-2025; 1990 lets the alert demo shift the track window back one year too


def coord(s: str) -> float:
    """'28.0N' -> 28.0, '96.9W' -> -96.9"""
    v = float(s[:-1])
    return -v if s[-1] in "SW" else v


def parse(text: str) -> list[tuple]:
    rows, storm = [], None
    for line in text.splitlines():
        p = [x.strip() for x in line.split(",")]
        if p[0].startswith("AL") and len(p[0]) == 8:          # header line
            storm = {"id": p[0], "name": p[1], "year": int(p[0][4:]), "seq": 0}
            continue
        if storm is None or storm["year"] < MIN_YEAR or len(p) < 7:
            continue
        storm["seq"] += 1
        rows.append((storm["id"], storm["name"], storm["year"], storm["seq"], f"{p[0]} {p[1]}",
                     p[3], coord(p[4]), coord(p[5]), int(p[6])))
    return rows


def main() -> None:
    conn = connect()
    resp = httpx.get(URL, headers={"User-Agent": "weather-risk-agent-demo"}, timeout=120)
    resp.raise_for_status()
    rows = parse(resp.text)
    conn.executemany(
        "INSERT INTO storm_track_points VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (storm_id, seq) DO UPDATE SET "
        "status = excluded.status, lat = excluded.lat, lon = excluded.lon, wind_kt = excluded.wind_kt", rows)
    conn.execute(
        "INSERT INTO ingest_runs (source, hub_id, request_url, start_date, end_date, fetched_at, row_count, status) "
        "VALUES (?, NULL, ?, ?, NULL, ?, ?, 'ok')",
        (SOURCE, URL, f"{MIN_YEAR}-01-01", datetime.now(timezone.utc).isoformat(timespec="seconds"), len(rows)))
    conn.commit()
    n_storms = len({r[0] for r in rows})
    print(f"{len(rows)} track points, {n_storms} storms ({MIN_YEAR}-{max(r[2] for r in rows)})")
    conn.close()


if __name__ == "__main__":
    main()
