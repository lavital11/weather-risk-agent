"""Data-quality report for weather_daily: rows, date range, gaps and NULLs per hub.

Run:  .venv\\Scripts\\python.exe -m weather_risk.ingest.report
"""

from datetime import date

from weather_risk.db import connect
from weather_risk.ingest.open_meteo import COLUMNS


def main() -> None:
    conn = connect()
    # COUNT(col) counts non-NULL values, so COUNT(*) - COUNT(col) = NULLs.
    null_exprs = ", ".join(f"COUNT(*) - COUNT({c}) AS {c}" for c in COLUMNS)
    rows = conn.execute(
        f"SELECT hub_id, COUNT(*) AS n, MIN(date) AS first, MAX(date) AS last, "
        f"SUM(date BETWEEN '2016-01-01' AND '2025-12-31') AS n_scoring, {null_exprs} "
        f"FROM weather_daily GROUP BY hub_id ORDER BY hub_id"
    ).fetchall()

    short = {"snowfall_cm": "snow", "precipitation_mm": "precip", "wind_gust_max_kmh": "gust",
             "temp_min_c": "tmin", "temp_max_c": "tmax"}
    print(f"{'hub':<13}{'rows':>6} {'first':>11} {'last':>11} {'gaps':>5} {'2016-25':>8}  NULLs: "
          + " ".join(f"{short[c]:>6}" for c in COLUMNS))
    for r in rows:
        # Gaps = calendar days between first and last that have no row.
        span = (date.fromisoformat(r["last"]) - date.fromisoformat(r["first"])).days + 1
        print(f"{r['hub_id']:<13}{r['n']:>6} {r['first']:>11} {r['last']:>11} {span - r['n']:>5} "
              f"{r['n_scoring']:>8}         " + " ".join(f"{r[c]:>6}" for c in COLUMNS))
    print(f"\nExpected days 2016-01-01..2025-12-31: {(date(2025, 12, 31) - date(2016, 1, 1)).days + 1}")
    conn.close()


if __name__ == "__main__":
    main()
