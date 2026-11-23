# Design details: Weather Risk Intelligence Agent

> Read [DESIGN.md](DESIGN.md) first: goal, scope, architecture (diagram, components, one chat turn),
> repository structure and data storage. This file only adds the reasoning behind them: design
> choices, the scoring model, alerts, data sources, evaluation and trade-offs.
> Decisions with dates: [PLAN.md](PLAN.md). Assumptions and data caveats: [ASSUMPTIONS.md](ASSUMPTIONS.md).

## 1. Design choices

**Why plan → execute → narrate (and not a free function-calling loop):** the control flow is fixed
and every step can be shown and tested. The eval can check the expected plan exactly, and the LLM
never produces a number. The trade-off is that unusual multi-step questions must fit the plan schema
(list of hubs + top N covers the example questions).

**What the narrator prompt requires:**
- use only numbers in the facts;
- give the rank next to every score;
- check the question's premise ("why is X high?" when it isn't);
- say "effectively tied" instead of naming a single #1 when scores are within 1 point;
- state the data limits that matter for this answer.

The planner can't name a hub, hazard or metric that doesn't exist, because the schema's allowed values
are built from the config files.

**Failure handling:**
- Invalid JSON is retried once with the error message, then the agent falls back to "please rephrase".
- A plan that can't be executed (e.g. compare with one hub) gets a clarifying reply.
- Out-of-scope and clarify replies are written by code, with no second LLM call.

**LLM:** Google Gemini (`gemini-4-hydrogen`, paid Tier 1, $10 cap). It supports JSON-schema output,
and all four capabilities we use were verified in `scripts/verify_gemini.py`. Switching provider
means rewriting `llm.py` only. The client is created on first use, so the app runs without a key
(everything except chat).

## 2. The KPI: hub weather-disruption score

Deterministic, configured in `config/scoring.toml`:

- **Signal score** = `min(value / bound, 1) × 100`, with **fixed bounds** (absolute, not relative to
  the other hubs). Adding a hub never changes another hub's score, and alerts can't fire because of re-normalization.
- **Hazard score** = weighted sum of components:

| Hazard | Components (weight) | Signals |
|---|---|---|
| Winter | weather 0.7, FEMA 0.3 | snow days ≥ 0.25 cm, heavy snow ≥ 5 cm, min ≤ −18 °C, ice days* |
| Flood | weather 0.7, FEMA 0.3 | rain ≥ 50 mm, rain ≥ 100 mm |
| Severe storm | hail 0.35, gusts 0.35, FEMA 0.3 | hail ≥ 1" and ≥ 2" within 50 km (days), gusts ≥ 80 km/h |
| Hurricane | storm tracks 0.7, FEMA 0.3 | tropical storms/hurricanes passing within 200 km (NOAA HURDAT2, **1991–2025**), FEMA hurricane events |
| Heat | weather 1.0 | days with max ≥ 35 °C (bound 50/yr), ≥ 37.8 °C (bound 16/yr). FEMA doesn't declare heat disasters |

  \*Ice day = precip ≥ 2 mm, max ≤ 1 °C, less than half of it fell as snow (0.7 cm snow per mm, measured in our data).
  FEMA events = distinct incidents with a **Major Disaster declaration with household aid in the hub's
  county** (rule D). This removes precautionary or evacuee-support declarations, e.g. Dallas's Harvey declaration.
- **Overall** = noisy-OR: `100 × (1 − Π(1 − hazard/100))`. Disruption happens if *any* hazard hits,
  so having no hurricanes doesn't offset hail. A hazard at 100 would saturate the overall score at 100;
  no hub is there today, but the top hubs come close (Dallas 99.1, Houston 98.8) and are reported as
  effectively tied.
- **Explanations:** the executor reports each signal's points within its hazard, each hazard's
  leave-one-out contribution to the overall score, and ranks among hubs (ties flagged, zero = no rank).

**Different time window for hurricane tracks:** every signal uses 2016–2025, except storm tracks,
which use **1991–2025 (35 years)**. Hurricanes are rare: in 10 years, counts are 0–3 per hub, too
noisy to rank on. The track window is set in config (`window_start_year`/`window_end_year`) and
stated in the agent's caveats. Mixing windows assumes the 1991–2025 storm climate is representative
of today, which is the same "past ≈ future" assumption as A9, over a longer period.

**Effectively tied:** overall scores less than **1 point** apart are flagged `effectively_tied` by the
executor. The agent then says the hubs are tied and explains how their profiles differ, instead of
naming a single #1. At the saturated top, the gap is noise.

Current result: **Dallas 99.1 and Houston 98.8 (effectively tied for #1)**, Miami 86.4 (#3), Chicago
76.4, Kansas City 75.7, Minneapolis 73.4, Denver 60.8, Columbus 56.2, Indianapolis 53.4, Atlanta 51.2.
They differ completely in profile: Dallas = heat (97.7) + hail; Houston = flood (89.5) + hurricane (75.0).

### Heat as a fifth hazard, and the limits of equal weights

**Why heat is included:** the assignment describes disruption as severe weather that "shuts hubs
down, delays shipments and costs us money". Heat rarely shuts a hub, but it delays shipments and costs
money over many days: crew work-rest limits, equipment and reefer strain, slower yard operations.

**Known limitations:**
- **Equal weights don't capture severity.** Heat reduces capacity over many days, while ice or a
  hurricane can close a hub outright. Under noisy-OR, a heat score of 97.7 counts as much as a hurricane
  score of 97.7. The fix is weights (or a severity factor) from the customer's own downtime and delay
  costs, the first open question for the customer (Q1). The agent states this caveat whenever heat matters.
- **The ranking moves a lot for one hub.** Dallas has by far the most hot days (48.6/yr ≥ 95 °F,
  15.7/yr ≥ 100 °F), so heat alone takes it from **#6 to #2**. No other hub changes position:

| Hub | Overall before heat | Heat score | Overall with heat |
|---|---|---|---|
| Houston | 100.0 (#1) | 38.1 | 100.0 (#1) |
| **Dallas** | **61.8 (#6)** | **97.7** | **99.1 (#2)** |
| Chicago | 76.2 (#2) | 0.9 | 76.4 (#3) |
| Kansas City | 73.6 (#3) | 8.0 | 75.7 (#4) |
| Minneapolis | 73.1 (#4) | 1.0 | 73.4 (#5) |
| Miami | 69.7 (#5) | 0.6 | 69.8 (#6) |
| Denver | 57.8 (#7) | 7.1 | 60.8 (#7) |
| Columbus | 56.2 (#8) | 0.0 | 56.2 (#8) |
| Indianapolis | 53.3 (#9) | 0.1 | 53.4 (#9) |
| Atlanta | 36.5 (#10) | 4.1 | 39.1 (#10) |

- **Saturation near 100 at the top.** Noisy-OR compresses the top. (The table above is before
  HURDAT2; with storm tracks, Houston is 98.8 and Dallas 99.1.) The gap between them says almost
  nothing, so the executor flags gaps under 1 point as effectively tied (above). For the top hubs, the
  per-hazard scores and their drivers carry the information, not the overall number.

### Alerts on the long-term score (bonus)

The alert watches **the same long-term score** the analysts use, not a separate forecast score.
Three things can change it, and the system watches each:

| What changes the score | How it's detected |
|---|---|
| A new FEMA declaration in a hub's county | The daily job re-pulls OpenFEMA (the only source that changes day to day) |
| The yearly window moving forward | **Auto window**: it ends at the last complete calendar year, but moves only once *every* source has that full year (Open-Meteo lags ~2 days; SPC hail and HURDAT2 come out months later). Until then it stays put, and the reason is logged with the snapshot (`weather_risk/window.py`) |
| A config change (thresholds, bounds, weights) | A fingerprint of `scoring.toml` is stored with each snapshot; alerts say "config changed" |

**Catch-up:** if the last complete year isn't in every source yet, the job fetches only what's missing:
- weather days after the last stored day;
- that year's SPC hail file;
- the newest HURDAT2 release. It's validated first, then swapped in as a whole table in one
  transaction, so if anything fails the old data is kept.

It reuses the ingest code (same throttling, retries, upserts) and logs what's still missing.

**Thresholds** (`[alerts]` in config, user-approved after measuring a real one-year window shift):
any overall **rank change** (labelled if the hubs are now effectively tied); **overall change ≥ 2.0**;
any **hazard change ≥ 10**; a **FEMA event that counts entering or leaving** the window. The normal
year-to-year drift is 0.1–1.5 overall and ≤ 4.7 per hazard, so the thresholds sit above it.

**Demo with real data, not fake data** (`POST /alerts/demo`): score the 2015–2024 window as the
baseline, then 2016–2025. Result: **5 alerts, 5 quiet hubs**:
- Dallas and Houston swap #1/#2 (now effectively tied).
- Houston's severe-storm score drops −30.4, because two 2015 FEMA storm/flood disasters leave the window.
- Chicago −2.7 overall.
- Indianapolis −4.9 and drops to #9; Columbus moves up to #8.

A second live check with no new data raised 0 alerts, as expected.

### Answer presentation (from testing the deployed app)
- **Layout:** the narrator writes a short direct answer, then one "- Hub (score, rank): drivers" line
  per hub. The page (`static/index.html`, `renderAnswer`) lays this out for reading:
  - the "Assuming you still mean …" line as a small italic note;
  - the first sentence as a **bold summary**, and any remaining intro text as a normal paragraph;
  - **one styled row per hub**, with the hub name in bold;
  - the caveats in a **separate, smaller section**.

  This is display only: the answer text from the API is unchanged. Everything is built from plain
  text nodes; **HTML from the model is never inserted** (only `**bold**` is turned into bold).
- **Rounding:** every signal value is rounded to 1 decimal in one place (`make_signal`), and scores are
  computed from the unrounded value, so rounding can't change a score.
- **Follow-ups say what they assumed:** the planner reports `carried_over` (e.g. "winter in the
  Midwest"), and code (not the LLM) adds "Assuming you still mean …" at the top, only when there is
  conversation history.

## 3. Data sources

| Source | What | Why | Key caveat |
|---|---|---|---|
| Open-Meteo archive | Daily snow, precip, gusts, min/max temp at each hub's airport point | No key, 10+ years daily | Modeled data: Denver 2025 snow ~1/3 below NWS, so ranks are more reliable than absolute values. Free tier is non-commercial |
| OpenFEMA v2 | Disaster declarations by county | No key; records actual impact | Sparse (0–4 events/hub); depends on politics and damage thresholds |
| NOAA SPC | Hail reports (lat/lon, size) | Hail is a major Dallas risk the other sources miss | Spotter/population bias (we count days, not reports) |
| NOAA NHC HURDAT2 | Atlantic storm tracks, 6-hourly positions and status | Direct hurricane exposure; FEMA alone was 0–2 events per hub | Center track only (no wind-field size); a 200 km radius stands in for the storm's reach |

## 4. Storage and deployment details

These add to the storage section in DESIGN.md:
- **FEMA rows are stored raw**, with declaration type and program flags, so the counting rule (A/B/D)
  is a config switch and needs no re-fetch.
- **Hail reports are stored for the whole US**; scoring selects the ones near each hub by distance.
- **Storm tracks** are stored from 1990 (592 storms) so the alert demo can shift the track window back one year.
- **NULL is stored as NULL:** "no data" is never confused with 0.

**Deployment (Render free plan):**
- **Why the demo password:** the URL is public, and every chat costs Gemini credits, which already
  ran out once during development. `/health` stays open so the sleeping instance can be woken without a password.
- **Free-plan limits, accepted for a demo:**
  - It sleeps after 15 minutes idle (~1 minute to wake).
  - The in-process daily alert job only runs while the instance is awake.
  - Each restart loses the alert baseline, so the first run after a restart only saves a new baseline.
- **In production:** a paid always-on instance or a Render Cron Job calling `POST /alerts/check`, plus
  persistent storage for sessions and alert state (see next steps).

## 5. Evaluation

- **Unit and validation tests (no LLM):** 50 tests, plus 1 documented expected failure. They cover the
  scoring math, the FEMA counting (rule D: Major Disaster declarations with household aid),
  noisy-OR saturation, executor results on real data (e.g. Denver 2025 = 38/365 = 10.4%), and known
  events. The DFW ice storms of 2022 and 2023 are detected; 2021 is a documented expected failure.
- **Live eval (`evals/`):** 25 cases: the 4 assignment questions, follow-ups, aliases, premise
  checks, out-of-scope questions, the "which hub to invest in" framing, and 3 **holdout** cases written
  after the last prompt change. Each case checks the plan, the facts, required/forbidden phrases, and
  **grounding: every number in the answer must appear in the facts.**
- **Final result (final code, 25 cases × 3 runs): 75/75 case-runs passed.** Every case passed 3/3,
  including all 3 holdouts. One holdout's plan check (`holdout_prioritize_pair`) was relaxed after
  seeing earlier results: it now accepts `rank` or `compare` for a two-hub question. That change is
  disclosed in the eval log.
- **What earlier runs found:**
  - real bugs, all fixed: the planner refused investment questions, and `top_n` could cut through a tie;
  - grader bugs: phrase lists that were too narrow.

  Every test change made after seeing a result is disclosed in the eval log.
- **Limits:** 3 runs is a small sample, and the grounding check lets integers 0–10 and the years
  2016–2026 through unchecked. Eval history: [PLAN.md](PLAN.md#eval-log).

## 6. Assumptions, uncertainty and scoping (summary)

Full list in [ASSUMPTIONS.md](ASSUMPTIONS.md). The ones that matter most:
- **Proxies, not closures:** "disruption days" and FEMA declarations stand in for hub downtime. We
  have no actual closure data. The first questions for the customer: which hazards cost the most
  (weights), and what counts as disruption (thresholds).
- **Hurricane:** storm tracks over 35 years (a longer window than the other hazards), plus a small
  FEMA sample. Track distance stands in for impact; it ignores storm size and intensity at the hub.
- **Modeled weather data:** reliable for ranking, biased for absolute values (snow). Hail reports
  favor dense metros.
- **Past ≈ future:** a 10-year window, no climate trend adjustment.
- **Decision support, not an investment decision:** in line with FEMA's own guidance for its risk
  index. Hub volume, upgrade cost and existing protections are outside this data.

## 7. Done and next steps

**Done (beyond the core requirements):** heat hazard, HURDAT2 hurricane tracks, alerts on the
long-term score, deployment (Render), voice conversation.

**Next steps:**
- **LLM-as-judge in the eval** instead of phrase matching. Phrase lists had to be widened twice for
  answers that were correct but worded differently.
- **Persistent storage on the host** (Postgres or a persistent disk), so chat history, alert state
  and data downloaded at run time survive restarts; plus an always-on instance or a cron job for the daily alert.
- **Test a cheaper model against the same eval:** the eval checks plans, facts and grounding, so a
  smaller Gemini model can be compared on cost and pass rate.
- **Severity weights** from the customer's downtime and delay costs (see heat above).
- **Per-hub business weights** (exposure × importance, e.g. volume).
- **A feels-like (humidity) heat signal.**
- **Station data** to correct the snow bias of modeled weather.
- **The FEMA National Risk Index** as an outside check (blocked from our network during development).

**Considered and dropped:** a short-term (7-day forecast) operational alert, in favor of alerting on
the long-term score the assignment asks about.
