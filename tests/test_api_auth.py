"""Demo-password middleware. No LLM: the app is used without its startup (lifespan),
so these tests only hit endpoints that don't need the agent."""

import pytest
from fastapi.testclient import TestClient

from weather_risk.api import app, basic_auth_ok
from weather_risk.config import DB_PATH

client = TestClient(app)   # not used as a context manager -> lifespan (Gemini client) doesn't start


def test_basic_auth_ok():
    import base64
    good = "Basic " + base64.b64encode(b"demo:s3cret").decode()
    assert basic_auth_ok(good, "demo", "s3cret")
    assert not basic_auth_ok(good, "demo", "other")
    assert not basic_auth_ok(None, "demo", "s3cret")
    assert not basic_auth_ok("Basic !!!notbase64", "demo", "s3cret")


@pytest.mark.skipif(not DB_PATH.exists(), reason="run the ingest first")
def test_password_protects_everything_but_health(monkeypatch):
    monkeypatch.setenv("DEMO_PASSWORD", "s3cret")
    assert client.get("/health").status_code == 200                       # open: wake-up URL
    r = client.get("/hubs")
    assert r.status_code == 401 and "Basic" in r.headers["www-authenticate"]
    assert client.get("/hubs", auth=("demo", "wrong")).status_code == 401
    assert client.get("/hubs", auth=("demo", "s3cret")).status_code == 200
    assert client.get("/", auth=("demo", "s3cret")).status_code == 200     # chat page


def test_no_password_set_means_no_auth(monkeypatch):
    monkeypatch.delenv("DEMO_PASSWORD", raising=False)
    assert client.get("/hubs").status_code == 200
