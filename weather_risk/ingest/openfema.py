"""Fetch FEMA disaster declarations for each hub's county into fema_declarations.

Run:  .venv\\Scripts\\python.exe -m weather_risk.ingest.openfema

One request per hub, filtered on the hub's own county. fipsCountyCode '000'
means tribal areas (finding F1), so those rows are never requested.
Rows are stored raw; scoring decides which ones count (rule D).
Safe to re-run (upsert on hub_id + declaration_id).

Attribution (required by OpenFEMA terms): "This product uses the Federal
Emergency Management Agency's OpenFEMA API, but is not endorsed by FEMA."
Endpoint: DisasterDeclarationsSummaries v2; access time is logged in ingest_runs.
"""

import time
from datetime import datetime, timezone

import httpx

from weather_risk.config import Hub, load_hubs
from weather_risk.db import connect

URL = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries"
SOURCE = "openfema-v2-DisasterDeclarationsSummaries"
START = "2015-01-01T00:00:00.000Z"   # incident start date; the scoring window filters again (2015 for the alert demo)
PAGE_SIZE = 1000                     # API default; max is 10,000
PAUSE_S = 1.6                        # limit: 200 requests / 5 min (1 per 1.5 s), 2 concurrent

# OpenFEMA field -> our column. Declaring it once keeps $select and the insert in sync.
FIELDS = {
    "femaDeclarationString": "declaration_id",
    "disasterNumber": "disaster_number",
    "incidentId": "incident_id",
    "declarationType": "declaration_type",
    "incidentType": "incident_type",
    "declarationTitle": "title",
    "incidentBeginDate": "incident_begin_date",
    "incidentEndDate": "incident_end_date",
    "declarationDate": "declaration_date",
    "ihProgramDeclared": "ih_declared",
    "iaProgramDeclared": "ia_declared",
    "paProgramDeclared": "pa_declared",
    "hmProgramDeclared": "hm_declared",
}
DATE_FIELDS = {"incidentBeginDate", "incidentEndDate", "declarationDate"}
BOOL_FIELDS = {"ihProgramDeclared", "iaProgramDeclared", "paProgramDeclared", "hmProgramDeclared"}


def to_column_value(field: str, value):
    if value is None:
        return None
    if field in DATE_FIELDS:
        return value[:10]           # '2021-02-11T00:00:00.000Z' -> '2021-02-11'
    if field in BOOL_FIELDS:
        return int(bool(value))     # SQLite has no boolean type
    return value


def fetch_hub(client: httpx.Client, hub: Hub) -> tuple[str, list[dict]]:
    flt = (f"fipsStateCode eq '{hub.state_fips}' and fipsCountyCode eq '{hub.county_fips}' "
           f"and incidentBeginDate ge '{START}'")
    records, skip, url = [], 0, ""
    while True:  # page with $skip in case a county ever has more than PAGE_SIZE rows
        resp = client.get(URL, params={"$filter": flt, "$select": ",".join(FIELDS),
                                       "$top": PAGE_SIZE, "$skip": skip})
        resp.raise_for_status()
        url = url or str(resp.url)
        page = resp.json()["DisasterDeclarationsSummaries"]
        records += page
        time.sleep(PAUSE_S)
        if len(page) < PAGE_SIZE:
            return url, records
        skip += PAGE_SIZE


def store(conn, hub: Hub, records: list[dict]) -> None:
    cols = ["hub_id", *FIELDS.values()]
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols[2:])  # all except the key
    conn.executemany(
        f"INSERT INTO fema_declarations ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
        f"ON CONFLICT (hub_id, declaration_id) DO UPDATE SET {updates}",
        [(hub.id, *(to_column_value(f, r.get(f)) for f in FIELDS)) for r in records],
    )


def log_run(conn, hub: Hub, url, n: int, status: str) -> None:
    conn.execute(
        "INSERT INTO ingest_runs (source, hub_id, request_url, start_date, end_date, fetched_at, row_count, status) "
        "VALUES (?, ?, ?, ?, NULL, ?, ?, ?)",
        (SOURCE, hub.id, url, START[:10], datetime.now(timezone.utc).isoformat(timespec="seconds"), n, status),
    )


def main() -> None:
    conn = connect()
    with httpx.Client(timeout=60) as client:
        for hub in load_hubs():
            try:
                url, records = fetch_hub(client, hub)
                store(conn, hub, records)
                log_run(conn, hub, url, len(records), "ok")
                print(f"  {hub.id:<13} {len(records)} declarations")
            except Exception as e:
                log_run(conn, hub, None, 0, f"error: {type(e).__name__}: {e}")
                print(f"  {hub.id:<13} FAILED: {e}")
            conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
