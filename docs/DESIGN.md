# Design: Weather Risk Intelligence Agent

**Goal:** help logistics analysts decide which distribution hubs are most exposed to weather
disruption and should be prioritized for resilience investment. The agent ranks, compares and
explains hubs in a chat. **All numbers come from deterministic code; the LLM only interprets the
question and explains the computed facts.**

**Scope (narrow on purpose):** 10 hubs (Chicago, Minneapolis, Indianapolis, Kansas City, Columbus,
Dallas, Houston, Miami, Atlanta, Denver) × 4 hazards (winter, flood, severe storms, hurricanes),
scored on 2016–2025. Out of scope: heat, wildfire, earthquake, hub business importance (volume).
Details and every decision with its reason: [PLAN.md](PLAN.md). Assumptions and data caveats:
[ASSUMPTIONS.md](ASSUMPTIONS.md).

---

## 1. System architecture

```mermaid
flowchart LR
    subgraph Offline["Offline (run ahead of time)"]
        OM[Open-Meteo archive] --> ING[Ingest scripts]
        FE[OpenFEMA API] --> ING
        SPC[NOAA SPC hail CSVs] --> ING
        ING --> DB[(SQLite<br/>weather_risk.db)]
    end
    subgraph Server["FastAPI server"]
        API["/chat /scores /hubs /health"] --> AG[Agent]
        AG -- "1. question + history" --> LLM[[Gemini via llm.py]]
        LLM -- "QueryPlan JSON" --> AG
        AG -- "2. plan" --> EX[Executor]
        EX --> SC[Scoring engine]
        SC --> DB
        EX -- "facts" --> AG
        AG -- "3. facts" --> LLM
        LLM -- "Answer JSON" --> AG
        API --> SS[(sessions.db)]
    end
    UI[Chat page<br/>static/index.html] -- "POST /chat" --> API
```

**Components and how they talk:**

| Component | File(s) | Role |
|---|---|---|
| Ingest | `weather_risk/ingest/*.py` | Fetches public data once and stores it raw in SQLite. Retries, rate-limit pauses, and a provenance log (`ingest_runs`). The demo never depends on a live third-party API. |
| Scoring engine | `weather_risk/scoring.py`, `config/scoring.toml` | Pure functions: stored data + config → a score per hub and hazard, each with its evidence (raw values, bounds, FEMA event titles). No LLM. |
| Executor | `weather_risk/agent/executor.py` | Runs a `QueryPlan` (rank / compare / explain / weather_stat) and returns **facts**: scores, ranks, drivers, premise check, statistics. |
| LLM client | `weather_risk/llm.py` | The only file that talks to Gemini. Requests JSON for a Pydantic schema, validates it, retries once with the error, then raises → the caller falls back safely. |
| Agent | `weather_risk/agent/agent.py`, `prompts.py`, `schemas.py` | One turn = **plan → execute → narrate**. Out-of-scope / clarify replies are written by code (no second LLM call). |
| API | `weather_risk/api.py` | FastAPI. `/chat` loads the session history, runs the agent, saves the turn. `/scores` returns the full deterministic ranking. |
| Chat UI | `static/index.html` | One page, no framework. Calls only `/chat`. A collapsible **"How I got this"** panel under each answer shows the plan and the facts. |

**One chat turn, step by step:**
1. The UI posts `{message, session_id}` to `/chat`. The API loads that session's earlier turns.
2. **Plan (LLM):** Gemini turns the question plus history into a `QueryPlan` JSON. Hub ids, hazards,
   regions and metrics are enums built from config, so it can't name a hub that doesn't exist.
   Follow-ups ("what about Chicago?") are resolved here.
3. **Execute (code):** the executor validates the plan (e.g. compare needs ≥ 2 hubs) and computes the facts.
4. **Narrate (LLM):** Gemini writes an `Answer` JSON (`answer`, `caveats`, `confidence`) from the facts
   only. The prompt requires: use only numbers in the facts, give the rank next to every score,
   check the question's premise, and state the data limits.
5. The API stores the turn and returns the answer + plan + facts to the UI.

**Why plan → execute → narrate (and not a free function-calling loop):** the control flow is fixed
and every step can be shown and tested. The eval can check the expected plan exactly, and the LLM
never produces a number. The trade-off is that unusual multi-step questions must fit the plan schema
(list of hubs + top N covers the example questions).

**LLM:** Google Gemini (`gemini-4-hydrogen`, paid Tier 1, $10 cap). It supports JSON-schema output,
and all four capabilities we use were verified in `scripts/verify_gemini.py`. Switching provider
means rewriting `llm.py` only.

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
| Hurricane | FEMA 1.0 | FEMA hurricane / tropical-storm events (small sample) |

  \*Ice day = precip ≥ 2 mm, max ≤ 1 °C, less than half of it fell as snow (0.7 cm snow per mm, measured in our data).
  FEMA events = distinct incidents with a **Major Disaster declaration with household aid in the hub's
  county** (rule D). This removes precautionary or evacuee-support declarations, e.g. Dallas's Harvey declaration.
- **Overall** = noisy-OR: `100 × (1 − Π(1 − hazard/100))`. Disruption happens if *any* hazard hits,
  so having no hurricanes doesn't offset hail. One hazard at 100 saturates the overall score (Houston).
- **Explanations:** the executor reports each signal's points within its hazard, each hazard's
  leave-one-out contribution to the overall score, and ranks among hubs (ties flagged, zero = no rank).

Current result (2016–2025): Houston 100 (#1), Chicago 76.2, Kansas City 73.6, Minneapolis 73.1,
Miami 69.7, Dallas 61.8 (#6, but #1 for hail and tied #2 for ice), Denver 57.8, Columbus 56.2,
Indianapolis 53.3, Atlanta 36.5.

## 3. Data sources

| Source | What | Why | Key caveat |
|---|---|---|---|
| Open-Meteo archive | Daily snow, precip, gusts, min/max temp at each hub's airport point | No key, 10+ years daily | Modeled data: Denver 2025 snow ~1/3 below NWS, so ranks are more reliable than absolute values. Free tier is non-commercial |
| OpenFEMA v2 | Disaster declarations by county | No key; records actual impact | Sparse (0–4 events/hub); depends on politics and damage thresholds |
| NOAA SPC | Hail reports (lat/lon, size) | Hail is a major Dallas risk the other sources miss | Spotter/population bias (we count days, not reports) |

## 4. Data storage choice

**SQLite**, one file: `data/weather_risk.db` (~13 MB), committed as a snapshot.

- **Why:** the data is small (~40k weather rows, 62 FEMA rows, ~88k hail reports) and read-mostly.
  It's queried with SQL, needs no server (zero setup on Windows or a host), and the committed snapshot
  makes the demo work offline from the data APIs.
- **Tables:**
  - `weather_daily` (hub_id, date, 5 variables; PK hub_id+date)
  - `fema_declarations` (raw, with declaration type and program flags, so counting rules A/B/D are a config switch)
  - `hail_reports` (all US reports; scoring selects by distance)
  - `ingest_runs` (provenance: URL, time, rows, status; used for attribution)
- **Re-runs:** ingest uses upserts, so re-running is idempotent. NULL is stored as NULL ("no data" ≠ 0).
- **Chat history** lives in a separate `data/sessions.db` (git-ignored), so the data snapshot never
  contains chat logs.
- **Alternatives considered:**
  - JSON/Parquet files: no queries, awkward for sessions and alert state.
  - Postgres: the production choice for multi-user writes, but setup overhead isn't justified here.
    The SQL is plain, so moving is straightforward.

## 5. Repository structure

```
config/          hubs.toml (hub registry), scoring.toml (every threshold, bound, weight)
data/            weather_risk.db (committed snapshot); sessions.db (git-ignored)
docs/            DESIGN.md (this), PLAN.md (decision log), ASSUMPTIONS.md
evals/           cases.toml (23 cases), run_eval.py, results/
scripts/         verify_gemini.py, explore_fema.py (phase-0 checks and exploration)
static/          index.html (chat page)
tests/           test_scoring.py, test_executor.py, test_validation.py (no LLM)
weather_risk/
  config.py db.py scoring.py llm.py sessions.py api.py
  ingest/        open_meteo.py openfema.py spc_hail.py report.py
  agent/         schemas.py prompts.py executor.py agent.py metrics.py cli.py
```

## 6. Evaluation

- **Unit and validation tests (no LLM):** 32 tests. They cover the scoring math, the rule-D counting,
  noisy-OR saturation, executor results on real data (e.g. Denver 2025 = 38/365 = 10.4%), and known
  events. The DFW ice storms of 2022 and 2023 are detected; 2021 is a documented expected failure.
- **Live eval (`evals/`):** 23 cases: the 4 assignment questions, follow-ups, aliases, premise
  checks, out-of-scope questions, the "which hub to invest in" framing, and 3 **holdout** cases written
  after the last prompt change. Each case checks the plan, the facts, required/forbidden phrases, and
  **grounding: every number in the answer must appear in the facts.**
- **Results:** 3 full runs, **68/69 case-runs passed**. 22 of the 23 cases passed 3/3, including 2 of the 3 holdouts.
  The one miss: `holdout_prioritize_pair` ("Between Atlanta and Denver, which one deserves resilience
  upgrades more?") passed 2/3. In one run the planner chose `rank` (restricted to those 2 hubs) instead
  of `compare`. The answer was still correct and grounded (Denver 57.8, #7, vs Atlanta 36.5, #10), so
  only the plan check failed. This is a side effect of the earlier prompt fix ("prioritization
  questions → rank"): the holdout caught the shift, which is what it was for.
- **Limits:** 3 runs is a small sample, and the grounding check lets integers 0–10 and the years
  2016–2026 through unchecked. Eval history: [PLAN.md](PLAN.md#eval-log).

## 7. Assumptions, uncertainty and scoping (summary)

Full list in [ASSUMPTIONS.md](ASSUMPTIONS.md). The ones that matter most:
- **Proxies, not closures:** "disruption days" and FEMA declarations stand in for hub downtime. We
  have no actual closure data. The first questions for the customer: which hazards cost the most
  (weights), and what counts as disruption (thresholds).
- **Hurricane = small sample:** FEMA-only, 1–2 events per hub. The answers say so. NOAA HURDAT2
  storm tracks are the planned fix.
- **Modeled weather data:** reliable for ranking, biased for absolute values (snow). Hail reports
  favor dense metros.
- **Past ≈ future:** a 10-year window, no climate trend adjustment.
- **Decision support, not an investment decision:** in line with FEMA's own guidance for its risk
  index. Hub volume, upgrade cost and existing protections are outside this data.

## 8. Not done / next steps

In priority order: scheduled alert on short-term (forecast) risk → HURDAT2 hurricane tracks → heat
hazard (max temperature already stored) → voice input → deployment. Also: per-hub business weights
(exposure × importance), station data to correct the snow bias, and the FEMA National Risk Index
as an outside check (blocked from our network).
