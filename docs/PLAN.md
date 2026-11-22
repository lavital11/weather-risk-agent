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
| — | Heat (bonus 1) | **5th hazard in the overall score.** Hot days ≥ 35 °C (bound 50) and ≥ 37.8 °C (bound 16), averaged; no feels-like signal; equal treatment. Dallas #6 → #2 (99.1). The Dallas premise eval case now expects the premise to hold; the heat eval case is now in scope | User decision: the assignment's disruption includes "delays shipments and costs us money", which heat causes. Limitations in DESIGN.md §2 |
| — | HURDAT2 (bonus 2) | Hurricane = storm tracks 0.7 (within 200 km at TS/HU strength, **1991–2025**, bound 8/10 yr) + FEMA 0.3. Results: Miami 77.4 > Houston 74.9 on hurricanes; Houston no longer saturated at 100; Dallas 99.1 vs Houston 98.8 at the top; Miami #6 → #3 | User-approved proposal; the different window is documented (A18, DESIGN §2) |
| — | Effectively tied | Overall gaps < 1 point → `effectively_tied` in the facts; the agent says "tied" instead of naming a single #1 | User decision: Dallas 99.1 vs Houston 98.8 is noise |
| — | Eval updates for HURDAT2 | Changed because the **model** changed, not after failures: `miami_vs_houston` (caveat now mentions the 1991 window), `dallas_premise` (must say "tied"), `top3_overall` and `no_investment_decision` (top hub dallas), `followup_why_top` (first hub may be Dallas or Houston; must say tied), `houston_saturation` → `houston_top_tie` (no saturation any more) | User approved updating these |
| — | Eval: tied top | `dallas_premise`, `top3_overall`, `no_investment_decision` no longer expect Dallas alone. They require **both Dallas and Houston** plus "tied" (or equivalent), and the facts check is `top_tied_includes = [dallas, houston]` | User decision: naming Houston first is equally correct when the two are tied |
| — | Alerts (bonus 3) | **Alert on the long-term score** (user changed my 7-day forecast proposal: that's what the assignment asks). Daily job: refresh FEMA → auto window → score → snapshot → compare → webhook. Thresholds: rank change, overall ≥ 2.0, hazard ≥ 10, FEMA event in/out. Config fingerprint per snapshot. Auto window moves only when every source has the full year, otherwise it stays put and logs why. Demo: 2015–24 vs 2016–25 on real data → 5 alerts | Thresholds approved after seeing the measured window-shift changes |
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
| 3 (×3 repeats, 23 cases incl. 3 holdouts) | 68/69 case-runs | `holdout_prioritize_pair` 2/3: once planned as `rank` of the 2 hubs instead of `compare`. The answer was correct and grounded; only the plan check failed. This is a side effect of the run-2 prompt fix, caught by the holdout. Test left unchanged (decision pending: accept `rank` or `compare` for this case?) |
| — | test change | **`holdout_prioritize_pair` relaxed after seeing the results** (user decision): the plan may be `rank` or `compare`, but must include both Atlanta and Denver. Why: the question is a two-hub ranking, so `rank` limited to those hubs is a valid plan, and the answer was correct and grounded in every failing run. Disclosed because loosening a test after it fails can hide real problems |
| 7 (4 tie cases, ×3, after the credit top-up) | 9/12, then 12/12 | `dallas_premise`, `top3_overall`, `followup_why_top` 3/3. `no_investment_decision` 0/3: all 3 answers were **correct** ("final investment choice should also weigh hub volume, upgrade costs, and existing protections", both tied hubs named), but the phrase list didn't include that wording. **Grader widened a second time** ("should also weigh", "hub volume"), after seeing the results, then 3/3. Lesson: phrase matching is brittle; an LLM-as-judge or a structured `decision_support` field in the Answer schema would be more robust |
| 6 (re-run of 4 tie cases, ×3) | **not run**: 0/12 | Every call failed with Gemini **402 "prepayment credits are depleted"**; no answer was graded (`evals/results/run6_credits_depleted_402.json`). Must be re-run after credits are added: `run_eval --repeat 3 dallas_premise top3_overall no_investment_decision followup_why_top` |
| 5 (HURDAT2 + tie flag, 25 cases, ×3) | **74/75** case-runs; 24/25 cases 3/3 | `no_investment_decision` 2/3. (a) **Grader gap:** the answer said "factors outside this data", not in the accepted phrases; widened (answer was correct, changed after seeing it). (b) **Executor gap:** the planner set `top_n = 1`, so Houston (tied with Dallas) was cut and couldn't be explained. Fixed: `top_n` never cuts through a tie (new unit test). Run 5 used the pre-tie versions of 3 cases; those were re-run 3× afterwards (next row) |
| 4 (heat added, 25 cases, ×1) | 24/25 | Only `holdout_prioritize_pair` failed again (plan `rank` vs `compare`; answer correct). All heat-related cases passed: `dallas_premise` (now holds, #2), `heat_ranking`, `atlanta_false_premise`, `wildfire_out_of_scope` |

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
| 7 | README, design doc, transcript, deploy | 2h | **Docs done** (DESIGN.md, README with attribution and retrieval times). Transcript: the user exports it at the end. Deploy: bonus | 0.5h transcript (user) |
| — | Voice (bonus) | — | Not started | 0.5h |

Phases 0+1 were estimated at 3.5h and will take about 4h. The extra time went into verification (rate limits, FEMA data quirks), which we judged worth it. The original 1.5h buffer is used up.

**Bonus order (user decision, after the design doc):** heat → HURDAT2 → alerts → deploy → voice.
**Workflow:** commit and push to the private GitHub repo after each phase. The user exports the AI-session transcript at the very end.

**Cut line.** Core work (phases 1–5 + README/design doc + transcript) is about 12h remaining. It must be done.
Above the cut line, in the order we drop it: voice (0.5h) → heat hazard (0.5h; `temp_max_c` already stored, weights unchanged unless decided) → HURDAT2 hurricane tracks (1h; hail, the first bonus, is done) → deploy (1h) → alerts (1.5h) → severe-storm hazard → advanced follow-up handling → fewer hubs.
Checkpoint: when phase 5 is done, start bonus work only if at least 3h of working time remain before the deadline, keeping 1h free for the docs and a rehearsal.

## Future improvements
- Extreme-heat hazard (days with max temperature ≥ a threshold, e.g. 38 °C / 100 °F), most relevant for Dallas and Houston.
- FEMA National Risk Index as an outside sanity check of the ranking (blocked from the dev network).
- Weight hubs by business importance (volume, revenue) → exposure × importance.
- Station data (NOAA, needs a token) to correct the reanalysis snow bias.
