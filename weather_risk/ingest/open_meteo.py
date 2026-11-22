"""Fetch daily weather history from the Open-Meteo archive into weather_daily.

Run:  .venv\\Scripts\\python.exe -m weather_risk.ingest.open_meteo

One API call per hub (10 years of daily data fits in one response).
Safe to re-run: existing (hub_id, date) rows are updated, not duplicated.
Data: Open-Meteo.com, CC BY 4.0 (free API = non-commercial use only).
"""

import sqlite3
import time
from datetime import date, datetime, timedelta, timezone

import httpx

from weather_risk.config import Hub, load_hubs
from weather_risk.db import connect

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
SOURCE = "open-meteo-archive"
START_DATE = date(2015, 1, 1)   # 2015 lets the alert demo compare the 2015-2024 and 2016-2025 windows

# Observed twice: the per-minute quota runs out after exactly 5 of these
# 10-year, 5-variable requests. Spacing calls 15 s apart (<= 4 per minute)
# keeps us under it. The 429 retry below stays as a safety net.
PAUSE_BETWEEN_HUBS_S = 15

# Open-Meteo variable name -> (our column name, expected unit).
# Units are checked on every response (verified in V1), so a silent API
# change (e.g. snowfall in inches) fails loudly instead of corrupting scores.
VARIABLES = {
    "snowfall_sum":       ("snowfall_cm",       "cm"),
    "precipitation_sum":  ("precipitation_mm",  "mm"),
    "wind_gusts_10m_max": ("wind_gust_max_kmh", "km/h"),
    "temperature_2m_min": ("temp_min_c",        "°C"),
    "temperature_2m_max": ("temp_max_c",        "°C"),
}
COLUMNS = [col for col, _unit in VARIABLES.values()]


def default_end_date() -> date:
    # The archive returns values even for today, which is still a partial day.
    # Today (UTC) minus 2 days is a finished day in every US time zone.
    return datetime.now(timezone.utc).date() - timedelta(days=2)


def get_with_retry(client: httpx.Client, params: dict, attempts: int = 5, wait_s: int = 65) -> httpx.Response:
    """GET with retry on two kinds of temporary failure:

    - HTTP 429 (rate limit): the limit resets within a minute, so wait just
      over a minute.
    - Connection errors (e.g. WinError 10054, "connection forcibly closed").
      They are intermittent and happen even on the very first request of a run,
      so the cause is the network or the server, not our pause. Wait briefly
      and retry; httpx opens a new connection. One hub needed 3 attempts, so we
      allow 5.
    """
    for attempt in range(1, attempts + 1):
        try:
            resp = client.get(ARCHIVE_URL, params=params)
        except httpx.TransportError as e:  # network-level failure, no HTTP response
            if attempt == attempts:
                raise
            print(f"    connection error ({e}); retrying in 5s, attempt {attempt}/{attempts}")
            time.sleep(5)
            continue
        if resp.status_code != 429 or attempt == attempts:
            resp.raise_for_status()  # any other HTTP 4xx/5xx -> exception
            return resp
        print(f"    429 rate limit ({resp.text[:200]}); waiting {wait_s}s, attempt {attempt}/{attempts}")
        time.sleep(wait_s)
    raise AssertionError("unreachable")


def fetch_hub(client: httpx.Client, hub: Hub, start: date, end: date) -> tuple[str, list[tuple]]:
    """Call the API for one hub. Returns (request URL, rows ready to insert)."""
    params = {
        "latitude": hub.lat,
        "longitude": hub.lon,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": ",".join(VARIABLES),
        "timezone": hub.timezone,  # makes each "day" a local calendar day
    }
    resp = get_with_retry(client, params)
    body = resp.json()

    units = body["daily_units"]
    for var, (_col, expected) in VARIABLES.items():
        if units.get(var) != expected:
            raise ValueError(f"{var}: expected unit {expected!r}, got {units.get(var)!r}")

    # The response has parallel lists: daily["time"][i] goes with daily[var][i].
    # Missing values arrive as JSON null -> Python None -> SQL NULL.
    daily = body["daily"]
    rows = [
        (hub.id, day, *(daily[var][i] for var in VARIABLES))
        for i, day in enumerate(daily["time"])
    ]
    return str(resp.url), rows


def store_rows(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    cols = ", ".join(COLUMNS)
    placeholders = ", ".join("?" * (2 + len(COLUMNS)))
    updates = ", ".join(f"{c} = excluded.{c}" for c in COLUMNS)
    # "Upsert": insert, or update the values if the (hub_id, date) row exists.
    conn.executemany(
        f"INSERT INTO weather_daily (hub_id, date, {cols}) VALUES ({placeholders}) "
        f"ON CONFLICT (hub_id, date) DO UPDATE SET {updates}",
        rows,
    )


def log_run(conn, hub: Hub, url: str | None, start: date, end: date, row_count: int, status: str) -> None:
    conn.execute(
        "INSERT INTO ingest_runs (source, hub_id, request_url, start_date, end_date, fetched_at, row_count, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (SOURCE, hub.id, url, start.isoformat(), end.isoformat(),
         datetime.now(timezone.utc).isoformat(timespec="seconds"), row_count, status),
    )


def main() -> None:
    start, end = START_DATE, default_end_date()
    print(f"Fetching {start} -> {end}")
    conn = connect()
    with httpx.Client(timeout=60) as client:
        for i, hub in enumerate(load_hubs()):
            if i > 0:
                time.sleep(PAUSE_BETWEEN_HUBS_S)
            # One hub failing (timeout, bad response) shouldn't stop the others;
            # the failure is recorded in ingest_runs.
            try:
                url, rows = fetch_hub(client, hub, start, end)
                store_rows(conn, rows)
                log_run(conn, hub, url, start, end, len(rows), "ok")
                print(f"  {hub.id:<13} {len(rows)} rows")
            except Exception as e:
                log_run(conn, hub, None, start, end, 0, f"error: {type(e).__name__}: {e}")
                print(f"  {hub.id:<13} FAILED: {e}")
            conn.commit()  # commit per hub: finished hubs are kept even if a later one fails
    conn.close()


if __name__ == "__main__":
    main()
