"""Exploration (before designing the FEMA table and mapping): which incident
types actually appear for our 10 hub counties, with incidents starting 2016-2025?

Run:  .venv\\Scripts\\python.exe scripts\\explore_fema.py

For each hub, two queries:
  - county rows:    fipsCountyCode = the hub's county
  - statewide rows: fipsCountyCode = '000' (whole-state declarations, e.g. COVID-19)
We show them separately, because whether statewide rows count is a decision.

OpenFEMA limits: 200 requests / 5 min per IP and 2 concurrent. We make 20
requests one at a time, with a pause between them.
"""

import time
from collections import Counter, defaultdict

import httpx

from weather_risk.config import load_hubs

URL = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries"
PAUSE_S = 1.6  # 200 req / 300 s = 1 request per 1.5 s at most
FIELDS = "disasterNumber,declarationType,incidentType,incidentBeginDate,fipsCountyCode,designatedArea"


def query(client: httpx.Client, state_fips: str, county_fips: str) -> list[dict]:
    flt = (
        f"fipsStateCode eq '{state_fips}' and fipsCountyCode eq '{county_fips}' "
        "and incidentBeginDate ge '2016-01-01T00:00:00.000Z' "
        "and incidentBeginDate lt '2026-01-01T00:00:00.000Z'"
    )
    resp = client.get(URL, params={"$filter": flt, "$select": FIELDS, "$top": 1000})
    resp.raise_for_status()
    rows = resp.json()["DisasterDeclarationsSummaries"]
    if len(rows) == 1000:
        raise RuntimeError("hit $top=1000; paging needed")
    time.sleep(PAUSE_S)
    return rows


def main() -> None:
    county_by_type = defaultdict(Counter)    # incidentType -> Counter(hub_id)
    statewide_by_type = defaultdict(Counter)
    decl_types = Counter()
    raw_rows = distinct = 0
    with httpx.Client(timeout=60) as client:
        for hub in load_hubs():
            for scope, county in (("county", hub.county_fips), ("statewide", "000")):
                rows = query(client, hub.state_fips, county)
                raw_rows += len(rows)
                # Check: can the same disaster appear twice for one area?
                seen = {r["disasterNumber"] for r in rows}
                distinct += len(seen)
                target = county_by_type if scope == "county" else statewide_by_type
                for r in rows:
                    target[r["incidentType"]][hub.id] += 1
                    decl_types[(scope, r["declarationType"])] += 1

    hub_ids = [h.id for h in load_hubs()]
    for title, table in (("COUNTY declarations", county_by_type), ("STATEWIDE (000) declarations", statewide_by_type)):
        print(f"\n{title}, incidents starting 2016-2025 (rows per hub)")
        print(f"{'incidentType':<20}{'total':>6}  " + " ".join(f"{h[:6]:>6}" for h in hub_ids))
        for itype, c in sorted(table.items(), key=lambda kv: -sum(kv[1].values())):
            print(f"{itype:<20}{sum(c.values()):>6}  " + " ".join(f"{c.get(h, 0):>6}" for h in hub_ids))
    print("\nDeclaration types (scope, type): count ->", dict(decl_types))
    print(f"Rows: {raw_rows}, distinct (area, disasterNumber): {distinct}")


if __name__ == "__main__":
    main()
