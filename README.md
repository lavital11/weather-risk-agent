# Weather Risk Intelligence Agent

An AI agent that helps logistics analysts decide which distribution hubs are most exposed to
weather disruption. **Scores are computed by deterministic code**; the LLM (Google Gemini) only
turns the question into a structured plan and explains the computed facts.

Scope: 10 hubs (5 Midwest, 4 South, Denver) × 4 hazards (winter, flood, severe storms,
hurricanes), 2016–2025. See `docs/PLAN.md` (decisions) and `docs/ASSUMPTIONS.md` (assumptions, data caveats).

## Run it (Windows, Python 3.12)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env        # then put your GEMINI_API_KEY in .env
.\.venv\Scripts\python.exe -m uvicorn weather_risk.api:app --port 8000
```
Open http://localhost:8000 for the chat, or http://localhost:8000/docs for the API.

The data snapshot `data/weather_risk.db` is committed, so no ingest is needed to run the demo.

### Rebuild the data (optional, ~5 min)
```powershell
.\.venv\Scripts\python.exe -m weather_risk.ingest.open_meteo
.\.venv\Scripts\python.exe -m weather_risk.ingest.openfema
.\.venv\Scripts\python.exe -m weather_risk.ingest.spc_hail
.\.venv\Scripts\python.exe -m weather_risk.ingest.report      # data-quality report
```

### Other commands
```powershell
.\.venv\Scripts\python.exe -m pytest -q                        # unit + validation tests (no LLM)
.\.venv\Scripts\python.exe -m weather_risk.scoring             # print the ranking
.\.venv\Scripts\python.exe -m weather_risk.agent.cli --debug   # terminal chat, shows plans
.\.venv\Scripts\python.exe -m evals.run_eval                   # 20-case eval with live Gemini (~2 min)
```
The eval checks, per case: the plan the LLM produced, the deterministic facts, key phrases and
caveats in the answer, and that every number in the answer appears in the facts.
Results: `evals/results/latest.json`.

## Data sources and attribution

- **Weather: [Open-Meteo.com](https://open-meteo.com/)** Historical Weather API (archive), licensed
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Weather data by Open-Meteo.com.
  The free API is for non-commercial use; a production deployment at a company would need
  Open-Meteo's commercial license.
- **Disaster declarations: FEMA OpenFEMA API**, dataset *Disaster Declarations Summaries – v2*
  (`https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries`). Access dates and times are
  recorded per request in the `ingest_runs` table.
  > This product uses the Federal Emergency Management Agency's OpenFEMA API, but is not endorsed
  > by FEMA. The Federal Government or FEMA cannot vouch for the data or analyses derived from these
  > data after the data have been retrieved from the Agency's website(s).
- **Hail: NOAA/NWS Storm Prediction Center (SPC)** severe weather database, yearly hail report files
  (`https://www.spc.noaa.gov/wcm/data/{year}_hail.csv`). Courtesy of NOAA's Storm Prediction Center.
- **County lookups (setup only): FCC Census Block API** (`https://geo.fcc.gov/api/census/`), used once to
  check each hub point's county.

Scores are estimates for decision support, not an investment decision (see `docs/ASSUMPTIONS.md`).
