"""SQLite connection and schema. One file: data/weather_risk.db."""

import sqlite3
from pathlib import Path

from weather_risk.config import DB_PATH

# "IF NOT EXISTS" makes this safe to run on every connect.
SCHEMA = """
CREATE TABLE IF NOT EXISTS weather_daily (
    hub_id            TEXT NOT NULL,   -- matches id in config/hubs.toml
    date              TEXT NOT NULL,   -- YYYY-MM-DD, the hub's local day
    snowfall_cm       REAL,            -- NULL = no data (not the same as 0)
    precipitation_mm  REAL,
    wind_gust_max_kmh REAL,
    temp_min_c        REAL,
    temp_max_c        REAL,
    PRIMARY KEY (hub_id, date)         -- one row per hub per day
);

-- One row per FEMA declaration for a hub's county. Stored raw: which rows
-- count as an "event", and for which hazard, is decided in scoring config
-- (rule D: DR + IH, distinct incident_id), so the rule can change without re-fetching.
CREATE TABLE IF NOT EXISTS fema_declarations (
    hub_id              TEXT NOT NULL,
    declaration_id      TEXT NOT NULL,   -- femaDeclarationString, e.g. 'DR-4586-TX'
    disaster_number     INTEGER,
    incident_id         TEXT,            -- shared by the EM + DR pair of one event
    declaration_type    TEXT,            -- DR (major disaster), EM (emergency), FM (fire)
    incident_type       TEXT,            -- e.g. 'Hurricane', 'Severe Ice Storm'
    title               TEXT,
    incident_begin_date TEXT,            -- YYYY-MM-DD
    incident_end_date   TEXT,
    declaration_date    TEXT,
    ih_declared         INTEGER,         -- 1/0: Individual (household) assistance in this county
    ia_declared         INTEGER,
    pa_declared         INTEGER,         -- Public assistance (agencies, incl. emergency measures)
    hm_declared         INTEGER,         -- Hazard mitigation
    PRIMARY KEY (hub_id, declaration_id)
);

-- NOAA SPC hail reports (all US). Scoring picks reports within a radius of each hub.
CREATE TABLE IF NOT EXISTS hail_reports (
    report_id   TEXT NOT NULL,     -- SPC 'om'
    date        TEXT NOT NULL,     -- YYYY-MM-DD (SPC local standard time)
    size_in     REAL,              -- hail diameter, inches
    lat         REAL,
    lon         REAL,
    state_fips  TEXT,
    county_fips TEXT,
    loss        TEXT,
    PRIMARY KEY (date, report_id)
);
CREATE INDEX IF NOT EXISTS hail_by_date ON hail_reports (date);

-- NOAA NHC HURDAT2 Atlantic best tracks: one row per 6-hourly position.
CREATE TABLE IF NOT EXISTS storm_track_points (
    storm_id TEXT NOT NULL,      -- e.g. 'AL092017'
    name     TEXT,               -- e.g. 'HARVEY'
    year     INTEGER NOT NULL,
    seq      INTEGER NOT NULL,   -- order of the point within the storm
    datetime TEXT,               -- 'YYYYMMDD HHMM' (UTC)
    status   TEXT,               -- TS = tropical storm (34-63 kt), HU = hurricane (>= 64 kt), TD, EX, ...
    lat      REAL,
    lon      REAL,
    wind_kt  INTEGER,
    PRIMARY KEY (storm_id, seq)
);
CREATE INDEX IF NOT EXISTS track_by_year ON storm_track_points (year);

CREATE TABLE IF NOT EXISTS ingest_runs (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,         -- e.g. 'open-meteo-archive'
    hub_id      TEXT,
    request_url TEXT,                  -- exact query, so numbers can be reproduced
    start_date  TEXT,
    end_date    TEXT,
    fetched_at  TEXT NOT NULL,         -- UTC ISO timestamp (attribution)
    row_count   INTEGER,
    status      TEXT NOT NULL          -- 'ok' or 'error: ...'
);
"""


def connect(path: Path = DB_PATH, check_same_thread: bool = True) -> sqlite3.Connection:
    # check_same_thread=False lets the API's worker threads share one read-only
    # connection; the executor guards it with a lock.
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row  # rows behave like dicts: row["hub_id"]
    conn.executescript(SCHEMA)
    return conn
