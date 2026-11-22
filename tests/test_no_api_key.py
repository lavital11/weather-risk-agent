"""A reviewer without a Gemini key: the app must start; only /chat needs the key."""

import pytest
from fastapi.testclient import TestClient

from weather_risk.config import DB_PATH


@pytest.mark.skipif(not DB_PATH.exists(), reason="run the ingest first")
def test_app_runs_without_gemini_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")          # empty: .env won't override an existing variable
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("DEMO_PASSWORD", raising=False)
    monkeypatch.setenv("ALERT_INTERVAL_HOURS", "0")
    from weather_risk.api import app
    with TestClient(app) as c:                         # runs the real startup
        assert c.get("/health").status_code == 200
        assert c.get("/scores").json()[0]["overall"] > 0
        r = c.post("/chat", json={"message": "Which hub is riskiest?"})
        assert r.status_code == 503 and "GEMINI_API_KEY" in r.json()["detail"]
