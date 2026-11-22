"""Fetch NOAA NHC HURDAT2 (Atlantic hurricane best tracks) into storm_track_points.

Run:  .venv\\Scripts\\python.exe -m weather_risk.ingest.hurdat2

Source: https://www.nhc.noaa.gov/data/hurdat/  (one text file per release, ~7 MB).
Format (checked against NHC's hurdat2-format-atlantic.pdf):
  header line:  AL092017, HARVEY, 74,          -> storm id (basin + number + year), name, number of points
  data line:    20170825, 1800, L, HU, 28.0N, 96.9W, 115, 938, ...
                date, time (UTC), record id (L = landfall), status, lat, lon, max wind (kt), ...
Status: TD < 34 kt, TS 34-63 kt, HU >= 64 kt, EX extratropical, SD/SS subtropical, LO/WV/DB other.

Finding a newer file (used by the alert job's catch-up, and by this script):
  1. read the folder listing, match  hurdat2-1851-<coverage year>-<release date>.txt
     (Atlantic only; release dates appear as MMDDYY or MMDDYYYY)
  2. take the newest coverage year, and within it the latest release
  3. skip if it's the file we already ingested (ingest_runs)
  4. validate: covers the expected year, and every storm has exactly the number of points its
     header declares
  5. swap the WHOLE table in one transaction (delete all + insert all): storms that NHC deleted or
     renamed in a revision don't stay behind, and if anything fails the old data is kept.
"""

import re
from datetime import date, datetime, timezone

import httpx

from weather_risk.db import connect

BASE = "https://www.nhc.noaa.gov/data/hurdat/"
FALLBACK_FILE = "hurdat2-1851-2025-092326.txt"     # the file verified on 2026-11-22
SOURCE = "nhc-hurdat2-atlantic"
MIN_YEAR = 1990  # scoring uses 1991-2025; 1990 lets the alert demo shift the track window back one year too
HEADERS = {"User-Agent": "weather-risk-agent-demo"}
FILE_RE = re.compile(r"hurdat2-1851-(\d{4})-(\d{6}|\d{8})\.txt")


class HurdatError(ValueError):
    """The file failed validation; the stored data is left unchanged."""


def coord(s: str) -> float:
    """'28.0N' -> 28.0, '96.9W' -> -96.9"""
    v = float(s[:-1])
    return -v if s[-1] in "SW" else v


def release_date(s: str) -> date:
    """'092326' (MMDDYY) or '02272026' (MMDDYYYY) -> date."""
    year = int(s[4:]) + (2000 if len(s) == 6 else 0)
    return date(year, int(s[:2]), int(s[2:4]))


def find_latest(listing_html: str) -> tuple[str, int] | None:
    """(file name, coverage year) of the newest Atlantic release in the folder listing, or None."""
    found = {(int(y), release_date(r), f"hurdat2-1851-{y}-{r}.txt") for y, r in FILE_RE.findall(listing_html)}
    if not found:
        return None
    year, _, name = max(found)          # newest coverage year first, then the latest release date
    return name, year


def parse(text: str, expected_year: int | None = None) -> list[tuple]:
    """Rows to store (storms from MIN_YEAR on). Raises HurdatError if the file doesn't check out."""
    rows, storm, max_year = [], None, 0

    def close(st):
        if st and st["year"] >= MIN_YEAR and st["seq"] != st["declared"]:
            raise HurdatError(f"{st['id']}: header declares {st['declared']} points, found {st['seq']}")

    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        p = [x.strip() for x in line.split(",")]
        try:
            if p[0].startswith("AL") and len(p[0]) == 8:           # header line
                close(storm)
                storm = {"id": p[0], "name": p[1], "year": int(p[0][4:]), "declared": int(p[2]), "seq": 0}
                max_year = max(max_year, storm["year"])
                continue
            if storm is None:
                raise HurdatError(f"line {n}: data before any storm header")
            storm["seq"] += 1
            if storm["year"] >= MIN_YEAR:
                rows.append((storm["id"], storm["name"], storm["year"], storm["seq"], f"{p[0]} {p[1]}",
                             p[3], coord(p[4]), coord(p[5]), int(p[6])))
        except (IndexError, ValueError) as e:
            if isinstance(e, HurdatError):
                raise
            raise HurdatError(f"line {n}: cannot parse ({e})") from e
    close(storm)
    if expected_year is not None and max_year != expected_year:
        raise HurdatError(f"file should cover {expected_year} but its last storm year is {max_year}")
    if not rows:
        raise HurdatError("no storms parsed")
    return rows


def swap_table(conn, rows: list[tuple], url: str) -> None:
    """Replace ALL track points in one transaction: all-or-nothing."""
    with conn:   # sqlite3: commits if the block succeeds, rolls back if anything raises
        conn.execute("DELETE FROM storm_track_points")
        conn.executemany("INSERT INTO storm_track_points VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        conn.execute(
            "INSERT INTO ingest_runs (source, hub_id, request_url, start_date, end_date, fetched_at, row_count, status) "
            "VALUES (?, NULL, ?, ?, NULL, ?, ?, 'ok')",
            (SOURCE, url, f"{MIN_YEAR}-01-01", datetime.now(timezone.utc).isoformat(timespec="seconds"), len(rows)))


def update(conn=None, force: bool = False) -> str:
    """Find the newest HURDAT2 release; if it's new and valid, swap it in. Returns a log line."""
    conn = conn or connect()
    try:
        listing = httpx.get(BASE, headers=HEADERS, timeout=60, follow_redirects=True)
        listing.raise_for_status()
        latest = find_latest(listing.text)
    except httpx.HTTPError as e:
        return f"tracks: could not read the NHC folder listing ({type(e).__name__}); kept current data"
    if latest is None:
        return "tracks: no HURDAT2 file found in the NHC listing (naming changed?); kept current data"
    name, year = latest
    url = BASE + name
    last = conn.execute("SELECT request_url FROM ingest_runs WHERE source = ? AND status = 'ok' "
                        "ORDER BY id DESC LIMIT 1", (SOURCE,)).fetchone()
    if last and last[0] == url and not force:
        return f"tracks: {name} is already ingested (covers through {year})"
    try:
        resp = httpx.get(url, headers=HEADERS, timeout=120)
        resp.raise_for_status()
        rows = parse(resp.text, expected_year=year)
        swap_table(conn, rows, url)
    except (httpx.HTTPError, HurdatError) as e:
        return f"tracks: {name} not used ({e}); kept current data"
    return f"tracks: ingested {name} ({len({r[0] for r in rows})} storms {MIN_YEAR}-{year})"


def main() -> None:
    print(update(force=True))


if __name__ == "__main__":
    main()
