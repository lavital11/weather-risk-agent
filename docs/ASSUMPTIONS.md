# Assumptions, Uncertainty and Open Questions

Running list; feeds the design doc.

## Assumptions
- **A1.** "Last year" = calendar year 2025 (project date: 2026-11-22).
- **A2.** Each hub is a single point at its **airport/logistics area** (e.g. DEN for Denver), chosen the same way for every hub. Weather uses the grid cell at that point; FEMA data uses the hub's county (FIPS).
  - **The county is the county of the hub point, not the city's main county.** Counties were checked with the FCC Census Block API.
  - Dallas = DFW → **Tarrant County** (not Dallas County).
  - Atlanta = ATL → Clayton County (not Fulton).
  - Kansas City = MCI → Platte County (not Jackson).
  - Columbus = CMH (Franklin County, the same county as the LCK cargo airport).
- **A3.** Long-term exposure (historical, for investment ranking) is kept separate from short-term risk (forecast/alerts, for the alert bonus).
- **A4.** A "disruption day" is a day above a hazard threshold (thresholds set in config). It stands in for closure risk and is not a record of actual closures.
- **A5.** FEMA disaster declarations stand in for impactful events. They also depend on politics and damage thresholds, and are county-level.
- **A6.** Hazard weights are equal by default (config). Real weights should come from the company's downtime-cost data.
- **A7.** Hub cities are illustrative; the company's real hub locations are unknown.
- **A8.** Open-Meteo is modeled (reanalysis) data with grid-scale error. **V6:** Denver 2025 snowfall was 51.5 cm (20.3") vs NWS 29.8", about 1/3 low. We assume the bias is similar across hubs, so **rankings are more reliable than absolute values**. This is why scoring uses snow-day counts (≥ 0.25 cm), not totals.
- **A9.** History window: 10 years. We assume the past decade is a reasonable guide to future exposure; climate trends could make this optimistic.
- **A10.** Regions such as "Midwest" use a fixed mapping (US Census regions), stored in hub config.
- **A11.** Hurricane exposure is a proxy (FEMA hurricane declarations + wind/rain extremes). Open-Meteo has no hurricane flag, and storm surge is not captured.
- **A12.** Open-Meteo archive recency. Tested 2026-11-22: the archive returned values **through today**, including the unfinished current day.
  - We store data only up to **today (UTC) − 2 days**, which is a finished day in every US time zone.
  - The most recent days may be preliminary and could be revised. We haven't verified this, so the ingest re-runs as an upsert.
  - "Current" data means "up to the latest stored day".
- **A13.** Absolute normalization: each signal is scaled linearly against a fixed bound in `config/scoring.toml` (e.g. 40 snow days/yr = 100) and capped at 100.
  - The bounds are judgment calls, set at roughly the highest value among our hubs, rounded.
  - **Bounds are fixed in config, never recomputed from the data**, so adding a hub doesn't change any other hub's score. That also prevents false alerts.
  - **Ice day:** precipitation ≥ 2 mm, max temperature ≤ 1 °C, and less than half of the precipitation fell as snow.
  - The units differ: precipitation is mm of liquid water, snowfall is cm of snow. We compare them using Open-Meteo's own ratio, **measured in our data: 0.70 cm of snow per mm of precipitation** on all-snow days (243 days with max ≤ −3 °C; range 0.15–0.88).
  - "Less than half as snow" = `snowfall_cm < 0.5 × 0.7 × precipitation_mm`.
- **A15. (Replaced by A18: storm tracks + FEMA.)** "FEMA events" means FEMA counting (rule D: Major
  Disaster declarations with household aid in the hub's county, one per incident). ~~Hurricane score = FEMA rule-D events only (100% weight), because the weather data has no usable hurricane signal (the tropical proxy also caught Midwest thunderstorms). It's a **small sample** (Miami 1 event, Houston 2), and the agent must say so. HURDAT2 storm tracks are planned as a bonus.~~

- **A14.** Hail and tornadoes don't appear in daily gridded weather data. For the severe-storm hazard, the weather signal is strong-gust days only; hail and tornado impact comes only from FEMA declarations.

## OpenFEMA findings (exploration 2026-11-22, `scripts/explore_fema.py`)
- **F1.** `fipsCountyCode = '000'` means **tribal areas** (e.g. Leech Lake Reservation, Seminole Tribe), not "statewide". These rows are excluded: they're not the hub's county.
- **F2.** One event can have both an Emergency (EM) and a Major Disaster (DR) declaration, e.g. Irma EM-3385 + DR-4337. These share an `incidentId`, so events are counted **by distinct `incidentId`**, not by rows.
- **F3.** `incidentType` doesn't always match the title. For example, "TROPICAL STORM ELSA" is typed Severe Storm, "TROPICAL STORM IMELDA" is typed Flood, and Harvey (mostly flooding) is typed Hurricane. The type → hazard mapping is approximate.
- **F4.** County-level declarations are **sparse**. 2016–2025 weather events for Denver, Indianapolis and Columbus: 0. No Midwest county had a winter declaration. So for winter, the score comes almost entirely from weather data, and FEMA mainly informs hurricane, flood and ice.
- **F6.** Dallas, Feb 2021 (the FEMA "Severe Ice Storm", DR-4586): in Open-Meteo it shows as **snow and cold, not ice**. On 2021-02-14 it shows 4.6 cm of snow; the minimum temperature reaches −17.8 °C on 02-16. The ice-day rule catches none of those days. The FEMA component still records the event. Dallas ranks mid-table, not "high", so the agent must check the premise of "why is Dallas high?" (eval case).
- **F7.** Ice rule validated against three known DFW ice shutdowns (`tests/test_validation.py`):
  - **Feb 2022:** detected (02-03).
  - **Jan–Feb 2023:** detected (01-30, 01-31, 02-01).
  - **Feb 2021:** missed (F6), recorded as an expected failure.
  - Open-Meteo's daily `weather_code` showed **no** freezing-drizzle or freezing-rain codes (56/57/66/67) on any of these dates. It showed rain and drizzle codes instead (51/53/55/61/63, plus 71/73 snow), so it can't detect ice for us.
  - A looser rule (precip ≥ 1 mm, min ≤ 0 °C, max ≤ 5 °C) also catches 2021, but it raises Midwest ice days ~10× (Chicago 0.3 → 4.4/yr), which is mostly false positives. We kept the stricter rule.
- **F5.** Non-weather types (Biological = COVID-19, Other = Surfside collapse) are excluded.

- **A16.** Hail = NOAA SPC hail reports. The signal is days per year with at least one report of hail ≥ 1" (NWS severe size) and ≥ 2", within 50 km of the hub point.
  - We count **days, not reports**, to damp spotter/population bias: dense metros like DFW generate more reports. The bias remains partly, so the hail signal likely favors metros.
  - Column meanings are inferred from the column names and values; SPC's description PDF is a scanned image.
  - The SPC site returns 403 to requests without a User-Agent header.
  - Severe-storm weights: hail 0.35, gusts 0.35, FEMA 0.3.

- **A17.** Heat = days with max ≥ 35 °C (bound 50/yr) and ≥ 37.8 °C (bound 16/yr), averaged; weather-only, because FEMA doesn't declare heat disasters.
  - Heat **slows** a hub (less capacity over many days) rather than shutting it, but it's treated equally with the other hazards. That's a known limitation: equal weights don't capture severity.
  - It moves Dallas from #6 to #2, and the top two are saturated near 100 (DESIGN_DETAILS.md §2).
  - It uses raw max temperature: a feels-like (humidity) signal was considered and skipped. Reanalysis tends to smooth extremes, so counts are likely conservative.

- **A18.** Hurricane exposure (replaces the FEMA-only A15): NOAA HURDAT2 storms passing within **200 km** of the hub while at tropical-storm or hurricane strength. Per 10 years, over **1991–2025**, bound 8 (Miami, the highest, has 7.1). Hazard = tracks 0.7 + FEMA 0.3.
  - **Different window:** 35 years instead of 2016–2025, because hurricanes are rare. This assumes the 1991–2025 storm climate represents today.
  - **Why 200 km:** 100 km missed Irma (2017) for Miami, because its center passed more than 100 km away while it still brought hurricane-force gusts.
  - **Limits:** this uses the center track only. It ignores wind-field size and the storm's strength at the hub's location; positions every 6 hours are interpolated linearly.
- **A19.** "Effectively tied": overall scores less than 1 point apart are reported as tied, not as a single #1. This is a judgment threshold for the saturated top of noisy-OR.

- **A20.** Alerts watch the long-term score.
  - **The daily job refreshes only FEMA.** Weather, hail and tracks change the score only through the yearly window, so they're re-ingested when a new full year is published; that's a manual step for now.
  - **When the window moves:** it advances only when all sources have the full year. A FEMA declaration for a December event that arrives in January is still caught later, as a "new FEMA event" alert.
  - **Thresholds** (rank change, ≥ 2.0 overall, ≥ 10 per hazard, FEMA event in/out) were set above the drift measured between the 2015–24 and 2016–25 windows. They're judgment calls.
- **Data now starts in 2015** (storm tracks in 1990), so the alert demo can compare two real windows. Scoring still uses 2016–2025 (tracks 1991–2025).

## Out of scope (hazards)
- ~~Extreme heat~~: **added later as a 5th hazard** (A17).
- Wildfire, earthquake, drought: not weather-disruption hazards in our question set; the agent answers "out of scope".

## Data-source caveats (for the design doc)
- **FEMA (NRI disclaimer/FAQ, as noted by the user):** FEMA does not guarantee accuracy, so our scores are estimates. NRI ignores impacts on transportation, supply chains and business operations, so county-level risk is only a proxy for hub disruption. FEMA advises against prioritizing funding from NRI alone, so we present our ranking the same way: **decision support, not an investment decision.**
- **OpenFEMA terms** (fema.gov/about/openfema/terms-conditions):
  - Attribution is required. Cite the endpoint with its version and the access date/time, and include the statement: "This product uses the Federal Emergency Management Agency's OpenFEMA API, but is not endorsed by FEMA." (The full required text continues with a note that FEMA cannot vouch for derived analyses. Copy it from the terms page into the README.)
  - No FEMA/DHS logos.
  - Data provided "as is".
  - No API key. Limit: 200 requests / 5 min per IP and 2 concurrent; 429 if exceeded.
- **Open-Meteo** (checked by the user):
  - The free API is non-commercial only, with limits of < 10k calls/day, 5k/hour and 600/min.
  - The data is CC BY 4.0 and needs attribution.
  - **A real deployment at a logistics company would need Open-Meteo's commercial license.**
  - Observed in the first ingest: a 10-year, 5-variable request uses up the per-minute quota fast. After 5 hubs we got "Minutely API request limit exceeded". The fetcher now spaces hubs 15 s apart (no 429 since) and keeps a 65 s retry on HTTP 429.
  - Also observed: intermittent connection resets (WinError 10054), even on the first request of a run. The fetcher retries up to 5 times. This doesn't affect the live demo, because the agent reads stored data; ingest runs ahead of time.
- **NWS API:** public domain; requires a User-Agent header.

## Open questions (for the customer)
- **Q1.** Which hazards cost the business most? (Sets the weights; equal for now.)
- **Q2.** Should ranking include hub importance (volume/revenue)? (Out of scope.)
- **Q6.** What counts as "disruption" operationally: closure, reduced throughput, delayed trucks? (Sets the thresholds.)
