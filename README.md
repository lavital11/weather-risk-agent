# Weather Risk Intelligence Agent

A chat agent that helps logistics analysts decide which distribution hubs are most exposed to
weather disruption and should be prioritized for resilience investment.

- **Scope:** 10 US hubs × 5 hazards (winter, flood, severe storms, hurricanes, extreme heat), 2016–2025.
- **How it works:** scores are computed by **deterministic code** from public data. The LLM (Google
  Gemini) only turns the question into a structured JSON plan and explains the computed facts.
- **Example questions:** "Which hubs in the Midwest are most exposed to winter disruption?",
  "Compare Miami and Houston in terms of hurricane and flood exposure.",
  "What percentage of days in Denver last year had snowfall?",
  "Why is the Dallas hub's weather disruption risk high?"

Docs:
- [DESIGN.md](docs/DESIGN.md): architecture, repo structure, data storage
- [DESIGN_DETAILS.md](docs/DESIGN_DETAILS.md): scoring model, alerts, evaluation, trade-offs
- [ASSUMPTIONS.md](docs/ASSUMPTIONS.md): assumptions, uncertainty, scoping
- [PLAN.md](docs/PLAN.md): decision log and eval log
- [OPERATIONS.md](docs/OPERATIONS.md): deployment, data refresh, alerts, voice

## Try it live

**https://weather-risk-agent-301a.onrender.com**. The **login details are in my submission**. No setup or
API key is needed.

1. Open https://weather-risk-agent-301a.onrender.com/health first (no login). The free server sleeps
   when idle, so it may take about a minute to wake up.
2. Open https://weather-risk-agent-301a.onrender.com and sign in. Click an example question or type your own. The agent also
   answers follow-up questions.
3. Under any answer, open **How I got this** to see the JSON plan and the computed facts behind it.
4. Optional:
   - **Voice:** click 🎤 and speak (Chrome or Edge).
   - **When an alert is sent:** when a hub's long-term risk changes:
     - its rank changes;
     - its overall score moves by 2+ points, or a hazard score by 10+ points;
     - a FEMA disaster declaration that counts enters or leaves the scoring window.

     The check runs once a day (while the free server is awake) and can be triggered manually with
     `POST /alerts/check`.
   - **Alerts demo:**
     1. Open https://weather-risk-agent-301a.onrender.com/docs.
     2. Choose `POST /alerts/demo`.
     3. Click **Try it out**, then **Execute**.

     - **What it does:** compares the 2015–2024 scoring window with 2016–2025, so the alerts come from
       a real change in the data, not test data.
     - **Expected result:** 5 alerts (Dallas, Houston, Chicago, Indianapolis, Columbus). Each lists its
       reasons and the overall score before and after. The other 5 hubs stay quiet.
     - **Proof they were sent:** run `GET /alerts/received` the same way. It shows what the webhook
       actually received.

## Run locally (Windows, Python 3.12)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env        # put your GEMINI_API_KEY in .env (needed for chat only)
.\.venv\Scripts\python.exe -m uvicorn weather_risk.api:app --port 8000
```
Open http://localhost:8000 for the chat, or http://localhost:8000/docs for the API.

- **API key:** chat needs your own Gemini key, free from [Google AI Studio](https://aistudio.google.com/apikey).
- **Without a key**, everything except chat works: `/scores`, the alerts and the unit tests. `/chat`
  then returns a clear "needs a GEMINI_API_KEY" message.
- **Data:** the snapshot in `data/weather_risk.db` is committed, so no data download is needed.

## Tests and evaluation

**Unit and validation tests:** no API key needed, runs in a few seconds.
```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

**Live eval (25 cases):** needs a Gemini key. One run is about 50 Gemini calls and 7–8 minutes.
```powershell
.\.venv\Scripts\python.exe -u -m evals.run_eval
```

**Live eval, 3 runs with per-case pass rates:** about 150 Gemini calls and 25 minutes.
```powershell
.\.venv\Scripts\python.exe -u -m evals.run_eval --repeat 3
```
- **What the eval checks, per case:** the plan the LLM produced, the computed facts, the key points
  and caveats in the answer, and that **every number in the answer appears in the facts**.
- **Result:** the last full run passed 75 of 75 case-runs; history in [PLAN.md](docs/PLAN.md#eval-log).
  Results are saved to `evals/results/latest.json`.

## Data sources and credits

- **Weather: [Open-Meteo.com](https://open-meteo.com/)** Historical Weather API, licensed
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Weather data by Open-Meteo.com.
  Retrieved November 22, 2026. The free API is for non-commercial use; a production deployment would
  need Open-Meteo's commercial license.
- **Disaster declarations: FEMA OpenFEMA API.** Federal Emergency Management Agency (FEMA), OpenFEMA
  Dataset: Disaster Declarations Summaries - v2. Retrieved from
  https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries on November 22, 2026, 14:27 UTC.
  > This product uses the Federal Emergency Management Agency's OpenFEMA API, but is not endorsed
  > by FEMA. The Federal Government or FEMA cannot vouch for the data or analyses derived from these
  > data after the data have been retrieved from the Agency's website(s).
- **Hail: NOAA/NWS Storm Prediction Center** severe weather database, yearly hail reports
  (`https://www.spc.noaa.gov/wcm/data/{year}_hail.csv`). Retrieved November 22, 2026.
- **Hurricane tracks: NOAA National Hurricane Center HURDAT2** Atlantic best tracks
  (`https://www.nhc.noaa.gov/data/hurdat/`), storms 1991–2025. Retrieved November 22, 2026.
- **County lookups (setup only): FCC Census Block API**, used once to check each hub's county.

Scores are estimates for decision support, not an investment decision.
