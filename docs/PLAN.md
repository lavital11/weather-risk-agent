# Plan and Decision Log

Working log for the take-home. Decisions are the user's; options and recommendations came from the AI session.

## Scope
- **Hubs (10):** Chicago, Minneapolis, Indianapolis, Kansas City, Columbus (Midwest); Dallas, Houston, Miami, Atlanta (South); Denver (West). Each hub is located at its airport/logistics-area point (see A2).
- **Hazards (4):** winter, tropical/hurricane, flood/heavy rain, severe storms (wind/hail/tornado).
- **Intents (5):** rank, compare, weather_stat, explain, out_of_scope/clarify. Follow-up questions are resolved from conversation history.

## Decisions
| ID | Topic | Decision | Why |
|---|---|---|---|
| D1 | LLM | Gemini (`gemini-4-hydrogen`, Tier 1 billing, $10 cap), called only through one client module | JSON-schema output and function calling; low cost; easy to swap provider |
| D2 | Data | Open-Meteo archive (weather) + OpenFEMA Disaster Declarations (impact events) + NWS API (short-term risk / alerts). No NRI. | No API keys needed; two independent signals for long-term exposure |
| D3 | Scoring | Per hazard: weather "disruption days" signal + FEMA declaration-frequency signal, combined by weights into 0-100; overall score = weighted sum across hazards | Deterministic and explainable down to raw counts |
| D3b/Q4 | Normalization | Absolute, with fixed bounds in config | Relative scores move when hubs are added and would trigger false alerts |
| Q1 | Weights | Equal weights in config | No customer cost data; first question for the customer |
| Q2 | Hub importance (volume) | Out of scope; mentioned in the design doc | |
| Q3 | History window | 10 years | Rare events (hurricanes) need a long window; the data is small |
| D4 | Agent pattern | **B: Plan → Execute → Narrate.** LLM returns a `QueryPlan` JSON → Pydantic validation (one retry, otherwise ask to clarify) → deterministic executor calls the scoring engine → LLM returns an `Answer` JSON from the computed facts only | Fixed, testable control flow; the eval can check the plan exactly; the LLM never produces numbers. Downside: multi-step questions must fit the plan schema (list of hubs + top N) |
| D5 | Storage | SQLite | Small, read-mostly data; a single file that can be committed as a demo snapshot |
| D6 | UI | Single HTML/JS page served by FastAPI | Clearly calls the API; browser speech API for voice |
| D7 | Alerts | APScheduler in FastAPI + `POST /alerts/check`, on the short-term score. Later phase. | |
| D8/Q5 | Deploy | Local first; platform decided later (Render is the main candidate) | |
| — | Weather date range | Fetch 2016-01-01 → latest available day. **Scoring uses complete years 2016–2025 only**; 2026 rows answer "so far this year" questions | Partial years would bias toward some seasons |
| — | Weather variables | snowfall_sum, precipitation_sum, wind_gusts_10m_max, temperature_2m_min, **temperature_2m_max** (for ice detection: precipitation on near-freezing days, e.g. Dallas Feb 2021), not for heat | |
| — | Heat | Out of scope (user decision): no example question asks for it; keeps scope narrow | |
| — | FEMA counting rule | **D: count distinct `incidentId` where the county got a Major Disaster (DR) with Individual Assistance (IH).** Exclude `fipsCountyCode='000'` (tribal areas). Store declaration type and program flags, so rules A/B stay a config switch | Measures local damage. Removes Dallas's precautionary/support hurricane declarations (Harvey DR without IH, Marco/Laura EM) while keeping the Feb 2021 ice storm (DR-4586 with IH). Sparse, so the weather data carries most of each hazard score |
| — | FEMA type → hazard | By `incidentType` only. Hurricane/Tropical Storm/Coastal Storm → hurricane; Flood → flood; Severe Ice Storm/Winter Storm/Snowstorm → winter; Severe Storm/Tornado/Straight-Line Winds → severe_storm; others (Biological, Other, Fire…) ignored | FEMA's own classification, deterministic. Known misfits (F3): Harvey → hurricane (mostly flood), Imelda → flood |
| — | Scoring model | Each hazard = weighted sum of **components**. `weather_days`: the average of its signals; each signal = days/yr scaled to its bound. `fema_events`: rule-D events per 10 yr scaled to its bound. Winter, flood and severe storm: weather 0.7, FEMA 0.3. Overall = equal-weight average of the 4 hazards. All bounds are fixed in `config/scoring.toml` | Approved; see A13 |
| — | Hurricane signal | **H2: FEMA only, weight 1.0**, flagged as a small sample. The engine takes components by type, so HURDAT2 can be added as a new component later | The weather proxy couldn't tell hurricanes from Midwest storms. H1 (HURDAT2) moved to the bonus list |
| — | Model gap: Dallas | The user flagged Dallas at #9 as a model gap. Ice rule validated (2 of 3 DFW events, F7) and kept. **Hail added** (SPC reports, A16) to severe storm. Result: Dallas #9 → #6, and severe storm 18.7 → 42.6, now the hub's top hazard | User's domain knowledge: DFW ice shutdowns 2021/22/23 and NRI hail losses |
| — | Overall score: noisy-OR | **overall = 100 × (1 − Π(1 − hazard/100))** replaces the equal-weight average (`[overall] method` in scoring.toml; the average is kept as an option). Hazard weights are unused under noisy-OR. "Drivers" of the overall score = leave-one-out contribution per hazard, split among its signals | User decision: disruption happens if **any** hazard hits, so having no hurricanes shouldn't offset hail. See the table below |
| — | Premise check (nuanced) | For "why is X high/low?", the executor computes deterministically whether the premise holds ("high" = top 3 of 10 hubs). It also lists the signals where the hub ranks #1–#2. Example answer: Dallas is #6 overall but #1 for hail and #2 for ice, and the equal hazard weights pull it to mid-table | User decision; weights unchanged |
| — | Dallas premise | The agent checks the premise of "why is X high?" against the actual rank. This is an eval case | F6 |
| — | Snow metric | Count snow days at ≥ 0.25 cm (~0.1", NWS "measurable"), not snowfall totals | V6: Open-Meteo totals ~1/3 below NWS for Denver 2025 |

## Overall score: average vs noisy-OR (2016–2025, rank next to score)

| Hub | Winter | Flood | Severe storm | Hurricane | Average (before) | Noisy-OR (after) | Rank change |
|---|---|---|---|---|---|---|---|
| Houston | 15.6 | 89.5 | 15.4 | 100.0 | 55.1 (#1) | **100.0 (#1)** | = |
| Chicago | 37.8 | 23.2 | 50.2 | 0.0 | 27.8 (#2) | 76.2 (#2) | = |
| Kansas City | 24.5 | 19.9 | 56.4 | 0.0 | 25.2 (#3) | 73.6 (#3) | = |
| Minneapolis | 56.3 | 14.5 | 28.1 | 0.0 | 24.7 (#4) | 73.1 (#4) | = |
| Miami | 0.0 | 29.7 | 13.7 | 50.0 | 23.4 (#5) | 69.7 (#5) | = |
| Dallas | 23.1 | 13.4 | 42.6 | 0.0 | 19.8 (#6) | 61.8 (#6) | = |
| Denver | 33.1 | 0.6 | 36.5 | 0.0 | 17.6 (#7) | 57.8 (#7) | = |
| Columbus | 25.5 | 2.4 | 39.7 | 0.0 | 16.9 (#8) | 56.2 (#8) | = |
| Indianapolis | 18.1 | 7.6 | 38.3 | 0.0 | 16.0 (#9) | 53.3 (#9) | = |
| Atlanta | 4.6 | 14.6 | 22.0 | 0.0 | 10.3 (#10) | 36.5 (#10) | = |

Notes for the design doc:
- **The ranking is unchanged** for these 10 hubs. Noisy-OR changes the *level* and the *meaning* of the score, not the order. It would reorder hubs whose hazard profiles differ more, e.g. a single-hazard hub against a multi-hazard hub with the same average.
- **Saturation:** Houston's hurricane score is 100 (2 FEMA events = the bound), so its overall is 100 whatever its other hazards are. This rests on a small sample (A15). Its flood score (89.5) alone would still make it #1.
- **Scores are higher across the board** (36–100 instead of 10–55). They are 0–100 index values, not probabilities; compare ranks, not absolute levels. The agent always shows the rank next to the score.

## Eval log
| Run | Result | Failures and what we did |
|---|---|---|
| 1 | 18/20 | `dallas_premise`: **grader bug**; the grounding check read "top-3" as −3, so the number regex was fixed. `no_investment_decision`: **agent bug**; the planner refused "which hub should we invest in?" as out of scope, so the planner prompt now treats prioritization questions as `rank`, and the narrator frames them as decision support (plus factors outside the data) |
| 2 | 20/20 | — |

Caveats on the eval:
- One run per case, and the LLM isn't fully deterministic, so 20/20 is one sample. Re-run before the demo.
- The prompt fix in run 2 was made after seeing the failing case. That's a small risk of fitting the prompt to the eval; the case's wording wasn't changed.
- The grounding check allows integers 0–10 and the years 2016–2026 without a match, because they're ranks, "of 10" and years. An invented small integer would therefore pass.

## Phase 0 verification results
| ID | Check | Result |
|---|---|---|
| V1 | Open-Meteo archive units | snowfall cm, precip mm, gusts km/h, temp °C |
| V2 | OpenFEMA v2 DisasterDeclarationsSummaries | Works. `$top` default 1,000, max 10,000; page with `$skip`. Limit: 200 requests / 5 min per IP, 2 concurrent. |
| V3 | NWS alerts by point | Works with a User-Agent header (empty FeatureCollection at test time) |
| V4 | Gemini model, structured output, function calling, tools+JSON combined | All 4 PASS (`scripts/verify_gemini.py`). SDK warns when `.text` is read from a response that contains a function call, so code must read `function_calls` / parts explicitly. |
| V5 | FEMA NRI | Blocked from the user's network (geo-block); dropped, listed as a future improvement |
| V6 | Open-Meteo vs NWS, Denver 2025, at DEN (39.86, -104.67) | 51.5 cm (20.3") vs 29.8" NWS (~1/3 low). 46 days with any snow (12.6%), 38 days ≥ 0.25 cm (10.4%) |

**Deadline:** assignment received ~10:50 Israel time 2026-11-22 → due **~10:50 on 2026-11-23**.

## Phases: status (2026-11-22 16:15 local, ~5.5h in)
| # | Phase | Original | Status | Remaining est. |
|---|---|---|---|---|
| 0 | Setup + API verification | 1h | **Done** (V1–V6, Gemini 4/4 PASS) | 0 |
| 1 | Hub config, fetchers, SQLite | 2.5h | **In progress**: hubs.toml ✓, Open-Meteo ingest ✓ (10 hubs, 0 gaps), FEMA exploration ✓; FEMA counting rule, hazard mapping, table and fetcher still to do | 1h |
| 2 | Scoring engine + unit tests | 2h | Not started (thresholds, bounds, ice rule are decisions) | 2.5h |
| 3 | Agent (plan → execute → narrate) | 3h | **Done** (live run of the 4 example questions + follow-up checked against facts; fix: zero scores get no rank, ties flagged) | 0 |
| 4 | FastAPI + chat UI | 2h | **Done**: /chat, /health, /hubs, /scores; per-session history in data/sessions.db (git-ignored); one-page chat with a "How I got this" panel (plan + facts); follow-up tested in the browser | 0 |
| 5 | Eval set + runner | 1.5h | **Done**: 20 cases (`evals/cases.toml`) checking plan, facts, answer and number grounding; run 1: 18/20, run 2: 20/20 (see Eval log) | 0 |
| 6 | Alerts (bonus) | 1.5h | Not started | 1.5h |
| 7 | README, design doc, transcript, deploy | 2h | Docs partly written as we go | 1.5h docs + 0.5h transcript (+1h deploy) |
| — | Voice (bonus) | — | Not started | 0.5h |

Phases 0+1 were estimated at 3.5h and will take about 4h. The extra time went into verification (rate limits, FEMA data quirks), which we judged worth it. The original 1.5h buffer is used up.

**Cut line.** Core work (phases 1–5 + README/design doc + transcript) is about 12h remaining. It must be done.
Above the cut line, in the order we drop it: voice (0.5h) → heat hazard (0.5h; `temp_max_c` already stored, weights unchanged unless decided) → HURDAT2 hurricane tracks (1h; hail, the first bonus, is done) → deploy (1h) → alerts (1.5h) → severe-storm hazard → advanced follow-up handling → fewer hubs.
Checkpoint: when phase 5 is done, start bonus work only if at least 3h of working time remain before the deadline, keeping 1h free for the docs and a rehearsal.

## Future improvements
- Extreme-heat hazard (days with max temperature ≥ a threshold, e.g. 38 °C / 100 °F), most relevant for Dallas and Houston.
- FEMA National Risk Index as an outside sanity check of the ranking (blocked from the dev network).
- Weight hubs by business importance (volume, revenue) → exposure × importance.
- Station data (NOAA, needs a token) to correct the reanalysis snow bias.
