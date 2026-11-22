# Operations: deployment, data refresh, alerts, voice

Details moved out of the README. For what the project is and how to run it, see [../README.md](../README.md).

## Deployed version (Render)

`render.yaml` deploys the app to Render's free plan.
- **Access:** the URL needs the demo password (user `demo`) for everything except `/health`. To test
  the password locally, set `DEMO_PASSWORD` in `.env`.
- **Sleep:** the free instance sleeps after 15 minutes idle. Open `/health` about a minute before using it.
- **Ephemeral disk:** chat history, alert state and anything downloaded while running (FEMA refreshes,
  catch-up data) are lost on restart and downloaded again by the next alert run.
- **Alerts:** the daily alert check only runs while the instance is awake.

## Rebuild the data (optional, ~5 min)

The data snapshot `data/weather_risk.db` is committed, so this isn't needed to run the demo.

```powershell
.\.venv\Scripts\python.exe -m weather_risk.ingest.open_meteo
.\.venv\Scripts\python.exe -m weather_risk.ingest.openfema
.\.venv\Scripts\python.exe -m weather_risk.ingest.spc_hail
.\.venv\Scripts\python.exe -m weather_risk.ingest.hurdat2
.\.venv\Scripts\python.exe -m weather_risk.ingest.report      # data-quality report
```
After a re-ingest, update the retrieval dates in the README's data-sources section. The exact time of
each request is in the `ingest_runs` table.

## Alerts (long-term score)

```powershell
.\.venv\Scripts\python.exe -m weather_risk.alerts --demo    # 2015-2024 vs 2016-2025 window, real data
.\.venv\Scripts\python.exe -m weather_risk.alerts           # what the daily job does (refresh FEMA, compare)
```
While the API runs:
- `POST /alerts/demo`, `POST /alerts/check`, `GET /alerts`;
- `GET /alerts/received`: what the built-in demo webhook receiver got.

Settings in `.env`:
- `ALERT_WEBHOOK_URL`: send alerts elsewhere, e.g. a Slack incoming webhook.
- `ALERT_INTERVAL_HOURS`: how often the API runs the check (default 24; 0 = off).

How it works: [DESIGN_DETAILS.md](DESIGN_DETAILS.md#alerts-on-the-long-term-score-bonus).

## Voice

Click 🎤 and speak your question (Chrome or Edge; allow the microphone). When you stop speaking it's
sent automatically, and the short direct answer is read aloud in an English (US) voice, female if the
browser has one. The full answer and list stay on screen. Typed questions stay text-only.
- **Browser speech service:** voice uses the browser's built-in speech features, and Chrome sends the
  audio to its own speech service.
- **Where it works:** on `localhost` and on the Render URL (HTTPS), but not on plain-HTTP addresses.

## Other commands

```powershell
.\.venv\Scripts\python.exe -m weather_risk.scoring             # print the ranking
.\.venv\Scripts\python.exe -m weather_risk.agent.cli --debug   # terminal chat, shows each plan
```
