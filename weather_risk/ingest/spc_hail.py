"""Fetch NOAA Storm Prediction Center (SPC) hail reports into hail_reports.

Run:  .venv\\Scripts\\python.exe -m weather_risk.ingest.spc_hail

Source: https://www.spc.noaa.gov/wcm/  ->  data/{year}_hail.csv (one file per year, ~1.2 MB).
Format checked on 2024_hail.csv. Columns used (meanings inferred from the column
names and values; SPC's description PDF is a scanned image we couldn't read):
  om = report id, date = YYYY-MM-DD, mag = hail diameter (inches),
  slat/slon = report location, stf = state FIPS, f1 = county FIPS, loss = loss field.
We store all US reports; scoring selects those near each hub.
Known bias: reports come from spotters and the public, so dense metros
(e.g. DFW) report more. Scoring counts hail *days*, not reports, to damp this.
"""

import csv
import io
import time
from datetime import datetime, timezone

import httpx

from weather_risk.db import connect

URL = "https://www.spc.noaa.gov/wcm/data/{year}_hail.csv"
SOURCE = "spc-hail-reports"
YEARS = range(2016, 2026)
HEADERS = {"User-Agent": "weather-risk-agent-demo"}  # the SPC site refused a request without one (403)


def fetch_year(client: httpx.Client, year: int) -> tuple[str, list[tuple]]:
    url = URL.format(year=year)
    resp = client.get(url)
    resp.raise_for_status()
    rows = []
    for r in csv.DictReader(io.StringIO(resp.text)):
        rows.append((r["om"], r["date"], float(r["mag"]), float(r["slat"]), float(r["slon"]),
                     r["stf"].zfill(2), r["f1"].zfill(3), r["loss"]))
    return url, rows


def main() -> None:
    conn = connect()
    with httpx.Client(timeout=120, headers=HEADERS) as client:
        for year in YEARS:
            try:
                url, rows = fetch_year(client, year)
                conn.executemany(
                    "INSERT INTO hail_reports (report_id, date, size_in, lat, lon, state_fips, county_fips, loss) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (date, report_id) DO UPDATE SET "
                    "size_in = excluded.size_in, lat = excluded.lat, lon = excluded.lon, loss = excluded.loss", rows)
                status = "ok"
                print(f"  {year}: {len(rows)} reports")
            except Exception as e:
                url, rows, status = URL.format(year=year), [], f"error: {type(e).__name__}: {e}"
                print(f"  {year}: FAILED {e}")
            conn.execute(
                "INSERT INTO ingest_runs (source, hub_id, request_url, start_date, end_date, fetched_at, row_count, status) "
                "VALUES (?, NULL, ?, ?, ?, ?, ?, ?)",
                (SOURCE, url, f"{year}-01-01", f"{year}-12-31",
                 datetime.now(timezone.utc).isoformat(timespec="seconds"), len(rows), status))
            conn.commit()
            time.sleep(1)  # be polite; 10 small files
    conn.close()


if __name__ == "__main__":
    main()
