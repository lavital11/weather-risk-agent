# Weather Risk Intelligence Agent

An AI agent that helps logistics analysts decide which distribution hubs are most exposed to
weather disruption. **Scores are computed by deterministic code**; the LLM (Google Gemini) only
turns the question into a structured plan and explains the computed facts.

Scope: 10 hubs (5 Midwest, 4 South, Denver) × 5 hazards (winter, flood, severe storms,
hurricanes, extreme heat), 2016–2025. See `docs/DESIGN.md` (architecture), `docs/PLAN.md` (decisions) and `docs/ASSUMPTIONS.md` (assumptions, data caveats).

> **Easiest way to try it: the live URL.** The URL and login details are in my submission. No setup
> or API key needed. The free server sleeps when idle: open `/health` first and wait about a minute.

## Run it locally (Windows, Python 3.12)

**You need your own Gemini API key for the chat.** It's free from
[Google AI Studio](https://aistudio.google.com/apikey); put it in `.env` as `GEMINI_API_KEY`.
**Without a key, everything except `/chat` still works:** the server starts, `/scores` gives the full
deterministic ranking, the alerts run (`/alerts/demo`, `python -m weather_risk.alerts --demo`), and the
unit tests pass (`pytest`). The chat page and `/chat` return a clear "needs a GEMINI_API_KEY"
message, and the live eval (`evals/`) also needs a key.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env        # then put your GEMINI_API_KEY in .env (optional, chat only)
.\.venv\Scripts\python.exe -m uvicorn weather_risk.api:app --port 8000
```
Open http://localhost:8000 for the chat, or http://localhost:8000/docs for the API.
**Voice:** click 🎤 and speak your question (Chrome or Edge; allow the microphone). When you stop
speaking it's sent automatically, and the short direct answer is read aloud in an English (US) voice,
female if the browser has one. The full answer and list stay on screen. Typed questions stay text-only.
This uses the browser's built-in speech features; Chrome sends the audio to its own speech service. It works on `localhost` and on the Render URL (HTTPS), but not on plain-HTTP addresses.

The data snapshot `data/weather_risk.db` is committed, so no ingest is needed to run the demo.

### Deployed version (Render)
`render.yaml` deploys the app to Render's free plan. The URL needs the demo password (user `demo`)
for everything except `/health`. The free instance sleeps after 15 minutes idle: open `/health` about
a minute before using it. Render's disk is ephemeral: chat history, alert state and anything downloaded
while running (FEMA refreshes, catch-up data) are lost on restart and downloaded again by the next
alert run. The daily alert check only runs while the instance is awake. Set locally with `DEMO_PASSWORD` in `.env` to test the password.

### Rebuild the data (optional, ~5 min)
```powershell
.\.venv\Scripts\python.exe -m weather_risk.ingest.open_meteo
.\.venv\Scripts\python.exe -m weather_risk.ingest.openfema
.\.venv\Scripts\python.exe -m weather_risk.ingest.spc_hail
.\.venv\Scripts\python.exe -m weather_risk.ingest.hurdat2
.\.venv\Scripts\python.exe -m weather_risk.ingest.report      # data-quality report
```

### Alerts (long-term score)
```powershell
.\.venv\Scripts\python.exe -m weather_risk.alerts --demo    # 2015-2024 vs 2016-2025 window, real data
.\.venv\Scripts\python.exe -m weather_risk.alerts           # what the daily job does (refresh FEMA, compare)
```
While the API runs: `POST /alerts/demo`, `POST /alerts/check`, `GET /alerts`, `GET /alerts/received`
(the built-in demo webhook receiver). Set `ALERT_WEBHOOK_URL` in `.env` to send alerts elsewhere
(e.g. a Slack incoming webhook). The API runs the check every `ALERT_INTERVAL_HOURS` (default 24).

### Other commands
```powershell
.\.venv\Scripts\python.exe -m pytest -q                        # unit + validation tests (no LLM)
.\.venv\Scripts\python.exe -m weather_risk.scoring             # print the ranking
.\.venv\Scripts\python.exe -m weather_risk.agent.cli --debug   # terminal chat, shows plans
.\.venv\Scripts\python.exe -u -m evals.run_eval                # 25 cases, live Gemini (~7-8 min, ~50 LLM calls)
.\.venv\Scripts\python.exe -u -m evals.run_eval --repeat 3     # 3 runs with per-case pass rates (~25 min)
```
The eval checks, per case: the plan the LLM produced, the deterministic facts, key phrases and
caveats in the answer, and that every number in the answer appears in the facts.
Results: `evals/results/latest.json`.

## Data sources and attribution

- **Weather: [Open-Meteo.com](https://open-meteo.com/)** Historical Weather API (archive), licensed
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Weather data by Open-Meteo.com.
  Retrieved November 22, 2026, 14:18 UTC.
  The free API is for non-commercial use; a production deployment at a company would need
  Open-Meteo's commercial license.
- **Disaster declarations: FEMA OpenFEMA API.** Citation:
  Federal Emergency Management Agency (FEMA), OpenFEMA Dataset: Disaster Declarations Summaries - v2.
  Retrieved from https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries on
  November 22, 2026, 14:27 UTC. (The exact time of each request is in the `ingest_runs` table;
  update this line after re-running the ingest.)
  > This product uses the Federal Emergency Management Agency's OpenFEMA API, but is not endorsed
  > by FEMA. The Federal Government or FEMA cannot vouch for the data or analyses derived from these
  > data after the data have been retrieved from the Agency's website(s).
- **Hail: NOAA/NWS Storm Prediction Center (SPC)** severe weather database, yearly hail report files
  (`https://www.spc.noaa.gov/wcm/data/{year}_hail.csv`). Courtesy of NOAA's Storm Prediction Center.
  Retrieved November 22, 2026, 14:57 UTC.
- **Hurricane tracks: NOAA National Hurricane Center HURDAT2** Atlantic best-track database
  (`https://www.nhc.noaa.gov/data/hurdat/hurdat2-1851-2025-092326.txt`), storms 1991–2025.
  Courtesy of NOAA's National Hurricane Center. Retrieved November 22, 2026.
- **County lookups (setup only): FCC Census Block API** (`https://geo.fcc.gov/api/census/`), used once to
  check each hub point's county.

Scores are estimates for decision support, not an investment decision (see `docs/ASSUMPTIONS.md`).
