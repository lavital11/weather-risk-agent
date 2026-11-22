"""HTTP API. The chat page uses only these endpoints.

Run:  .venv\\Scripts\\python.exe -m uvicorn weather_risk.api:app --port 8000
Then open http://localhost:8000  (API docs: http://localhost:8000/docs)

Endpoints:
  GET  /             chat page (static/index.html)
  GET  /health       liveness + data freshness
  GET  /hubs         hub registry
  GET  /scores       full deterministic ranking (no LLM)
  POST /chat         {message, session_id?} -> answer + plan + facts
"""

import asyncio
import base64
import logging
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from weather_risk import alerts, sessions
from weather_risk.agent.agent import Agent
from weather_risk.config import ROOT, load_hubs
from weather_risk.db import connect

STATIC = ROOT / "static"
state: dict = {"received": []}      # "received": payloads our demo webhook receiver got
log = logging.getLogger("uvicorn.error")


ALERT_INTERVAL_HOURS = float(os.environ.get("ALERT_INTERVAL_HOURS", "24"))   # 0 = no scheduled job


async def daily_alert_job():
    """Scheduled alert check: refresh FEMA, recompute, compare, notify. Runs in a worker thread."""
    while True:
        await asyncio.sleep(ALERT_INTERVAL_HOURS * 3600)
        try:
            res = await asyncio.to_thread(alerts.run)
            log.info("scheduled alert check: %s, %d alert(s)", res["window_note"], len(res["alerts"]))
            state["agent"] = Agent()   # new data/window -> rebuild cached scores for chat
        except Exception:
            log.exception("scheduled alert check failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    state["agent"] = Agent()   # loads data and computes all scores once, at startup
    task = asyncio.create_task(daily_alert_job()) if ALERT_INTERVAL_HOURS > 0 else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="Weather Risk Intelligence Agent", lifespan=lifespan)

# ------------------------------------------------------------------ demo password (deployed only)
# If DEMO_PASSWORD is set (on Render), every endpoint except /health needs HTTP basic auth.
# Reason: the URL is public and each chat costs Gemini credits. /health stays open so the
# sleeping free instance can be woken without a password. Unset locally -> no auth.
OPEN_PATHS = {"/health"}


def basic_auth_ok(header: str | None, user: str, password: str) -> bool:
    if not header or not header.startswith("Basic "):
        return False
    try:
        got_user, _, got_pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
    except (ValueError, UnicodeDecodeError):
        return False
    # compare_digest: constant-time comparison, doesn't leak how many characters matched
    return secrets.compare_digest(got_user, user) and secrets.compare_digest(got_pw, password)


@app.middleware("http")
async def require_demo_password(request: Request, call_next):
    password = os.environ.get("DEMO_PASSWORD")
    if password and request.url.path not in OPEN_PATHS:
        if not basic_auth_ok(request.headers.get("authorization"), os.environ.get("DEMO_USER", "demo"), password):
            # The WWW-Authenticate header makes the browser show its login prompt.
            return Response("Authentication required", status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="weather-risk-agent"'})
    return await call_next(request)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    session_id: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    caveats: list[str]
    confidence: str
    status: str
    plan: dict | None
    facts: dict | None


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    """Open without the password: used to wake the free Render instance before a demo."""
    conn = connect()
    last = conn.execute("SELECT MAX(date) FROM weather_daily").fetchone()[0]
    conn.close()
    return {"status": "ok", "weather_data_through": last, "hubs": len(load_hubs()),
            "agent_ready": "agent" in state}


@app.get("/hubs")
def hubs():
    return [h.__dict__ for h in load_hubs()]


@app.get("/scores")
def scores():
    ex = state["agent"].executor
    return sorted((s.model_dump() for s in ex.scores.values()), key=lambda s: -s["overall"])


# A plain "def" (not async): FastAPI runs it in a worker thread, so the slow
# LLM calls don't block other requests.
@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    session_id = req.session_id or sessions.new_session_id()
    history = sessions.load_history(session_id)
    try:
        reply = state["agent"].ask(req.message, history)
    except Exception as e:  # e.g. Gemini unreachable: report it, don't crash the server
        raise HTTPException(status_code=502, detail=f"agent error: {type(e).__name__}: {e}")
    sessions.save_turn(session_id, req.message, reply.plan, reply.answer.answer, reply.status)
    return ChatResponse(session_id=session_id, answer=reply.answer.answer, caveats=reply.answer.caveats,
                        confidence=reply.answer.confidence, status=reply.status,
                        plan=reply.plan.model_dump(exclude_none=True) if reply.plan else None, facts=reply.facts)


# ------------------------------------------------------------------ alerts (long-term score)

@app.post("/alerts/check")
def alerts_check(refresh_fema: bool = True):
    """What the daily job does, on demand: refresh FEMA, recompute, compare with the last snapshot."""
    res = alerts.run(refresh_fema=refresh_fema)
    state["agent"] = Agent()
    return res


@app.post("/alerts/demo")
def alerts_demo():
    """Real change on real data: the 2015-2024 window vs 2016-2025."""
    return alerts.run_demo()


@app.get("/alerts")
def alerts_list(limit: int = 50):
    return alerts.recent_alerts(limit)


@app.post("/alerts/webhook-receiver")
def webhook_receiver(payload: dict):
    """Stand-in for Slack/Teams/email: stores what the alert webhook sent (default ALERT_WEBHOOK_URL)."""
    state["received"] = ([payload] + state["received"])[:20]
    return {"ok": True}


@app.get("/alerts/received")
def webhook_received():
    return state["received"]
