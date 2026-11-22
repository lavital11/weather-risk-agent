"""Catalog of factual weather statistics the agent can compute for one hub and period.

Each metric is a day-level test on weather_daily (or hail days from hail_reports).
Thresholds match the scoring config where they overlap, so a statistic and a
score never disagree about what "a snow day" is.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Metric:
    label: str
    sql: str | None = None       # condition on a weather_daily row; None = special case
    total_column: str | None = None
    unit: str | None = None


METRICS: dict[str, Metric] = {
    "snow_days":         Metric("Days with measurable snow (>= 0.25 cm, ~0.1 in)", "snowfall_cm >= 0.25", "snowfall_cm", "cm"),
    "any_snow_days":     Metric("Days with any snowfall (> 0 cm)", "snowfall_cm > 0", "snowfall_cm", "cm"),
    "heavy_snow_days":   Metric("Heavy-snow days (>= 5 cm)", "snowfall_cm >= 5"),
    "rain_days":         Metric("Days with precipitation >= 1 mm", "precipitation_mm >= 1", "precipitation_mm", "mm"),
    "heavy_rain_days":   Metric("Heavy-rain days (>= 50 mm)", "precipitation_mm >= 50", "precipitation_mm", "mm"),
    "freezing_days":     Metric("Days with min temperature <= 0 C", "temp_min_c <= 0"),
    "extreme_cold_days": Metric("Extreme-cold days (min <= -18 C / 0 F)", "temp_min_c <= -18"),
    "ice_days":          Metric("Likely freezing-rain/ice days (scoring rule)",
                                "precipitation_mm >= 2 AND temp_max_c <= 1 AND snowfall_cm < 0.35 * precipitation_mm"),
    "strong_gust_days":  Metric("Strong-gust days (>= 80 km/h)", "wind_gust_max_kmh >= 80"),
    "hail_days":         Metric("Days with hail >= 1 in reported within 50 km (NOAA SPC)"),
}
