"""Alerts on the LONG-TERM score (user decision; this is what the assignment asks for).

What can change the score:
  - a new FEMA declaration in a hub's county  -> the daily job re-pulls OpenFEMA
  - the yearly window moving forward          -> auto window (weather_risk/window.py)
  - a config change                           -> config fingerprint stored with each snapshot

Each run: (optionally) refresh FEMA -> pick the window -> score all hubs -> save a snapshot ->
compare with the previous snapshot -> one alert per hub that crossed a threshold -> POST to the
webhook. Thresholds are in config/scoring.toml [alerts].

Run:  .venv\\Scripts\\python.exe -m weather_risk.alerts            (a real check, like the daily job)
      .venv\\Scripts\\python.exe -m weather_risk.alerts --demo     (2015-2024 vs 2016-2025, real data)
State lives in data/alerts.db (git-ignored), so the committed data snapshot stays clean.
"""

import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

import httpx

from weather_risk.agent.executor import TIE_GAP, competition_rank
from weather_risk.config import CONFIG_DIR, ROOT, load_hubs
from weather_risk.db import connect
from weather_risk.scoring import load_scoring_config, score_all
from weather_risk.window import resolve_window, shift_window

ALERTS_DB = ROOT / "data" / "alerts.db"
def default_webhook() -> str:
    """Our own demo receiver, on whatever port the server runs (Render sets PORT, default 10000)."""
    return f"http://127.0.0.1:{os.environ.get('PORT', '8000')}/alerts/webhook-receiver"

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, kind TEXT NOT NULL,   -- 'live' or 'demo'
    window TEXT NOT NULL, window_note TEXT, config_hash TEXT NOT NULL, data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, snapshot_id INTEGER, prev_snapshot_id INTEGER,
    hub_id TEXT NOT NULL, reasons_json TEXT NOT NULL, delivery TEXT
);
"""


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(ALERTS_DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def config_hash() -> str:
    """Fingerprint of scoring.toml: a change in thresholds/bounds/weights explains a score change."""
    return hashlib.sha256((CONFIG_DIR / "scoring.toml").read_bytes()).hexdigest()[:12]


def snapshot(conn: sqlite3.Connection, cfg: dict) -> dict:
    """Plain-dict summary of the scores: what we compare between runs."""
    scores = score_all(conn, cfg)
    ranks = competition_rank({s.hub_id: s.overall for s in scores})
    return {s.hub_id: {
        "name": s.hub_name, "overall": s.overall, "rank": ranks[s.hub_id],
        "hazards": {z: h.score for z, h in s.hazards.items()},
        "fema_events": {e.incident_id: e.title for h in s.hazards.values() for c in h.components for e in c.events},
    } for s in scores}


def compare(old: dict, new: dict, th: dict, old_hash: str, new_hash: str) -> dict[str, list[str]]:
    """{hub_id: [reasons]} for hubs that crossed a threshold. Pure function (unit-tested)."""
    out: dict[str, list[str]] = {}
    for h, n in new.items():
        o, reasons = old.get(h), []
        if o is None:
            continue                                            # new hub: nothing to compare with
        if th["rank_change"] and o["rank"] != n["rank"]:
            tied = [new[x]["name"] for x in new if x != h and abs(new[x]["overall"] - n["overall"]) < TIE_GAP]
            reasons.append(f"rank #{o['rank']} -> #{n['rank']}" + (f" (now effectively tied with {', '.join(tied)})" if tied else ""))
        d = round(n["overall"] - o["overall"], 1)
        if abs(d) >= th["overall_change"]:
            reasons.append(f"overall score {o['overall']} -> {n['overall']} ({d:+})")
        for z, s in n["hazards"].items():
            dz = round(s - o["hazards"].get(z, 0.0), 1)
            if abs(dz) >= th["hazard_change"]:
                reasons.append(f"{z} score {o['hazards'].get(z, 0.0)} -> {s} ({dz:+})")
        if th["fema_events"]:
            for i in sorted(set(n["fema_events"]) - set(o["fema_events"])):
                reasons.append(f"new FEMA event counts: {n['fema_events'][i]} ({i})")
            for i in sorted(set(o["fema_events"]) - set(n["fema_events"])):
                reasons.append(f"FEMA event left the window: {o['fema_events'][i]} ({i})")
        if reasons:
            if old_hash != new_hash:
                reasons.append(f"note: scoring config changed ({old_hash} -> {new_hash})")
            out[h] = reasons
    return out


def deliver(payload: dict) -> str:
    url = os.environ.get("ALERT_WEBHOOK_URL") or default_webhook()
    # Our own receiver sits behind the demo password (if set), so authenticate to it.
    auth = None
    if url == default_webhook() and os.environ.get("DEMO_PASSWORD"):
        auth = (os.environ.get("DEMO_USER", "demo"), os.environ["DEMO_PASSWORD"])
    try:
        r = httpx.post(url, json=payload, timeout=15, auth=auth)
        return f"{r.status_code} {url}"
    except httpx.HTTPError as e:   # e.g. the API (receiver) isn't running: keep the alert, record why
        return f"failed: {type(e).__name__} {url}"


def run(kind: str = "live", refresh_fema: bool = True, cfg_override: dict | None = None,
        baseline_cfg: dict | None = None) -> dict:
    data_conn, db = connect(), _db()
    if refresh_fema:
        from weather_risk.ingest import openfema
        openfema.main()                                         # the only source that changes daily
    n_hubs = len(load_hubs())
    cfg, note = (cfg_override, "demo window") if cfg_override else resolve_window(data_conn, load_scoring_config(), n_hubs)
    h = config_hash()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if baseline_cfg is not None:                                # demo: store the older window first
        db.execute("INSERT INTO snapshots (created_at, kind, window, window_note, config_hash, data_json) VALUES (?,?,?,?,?,?)",
                   (now, kind, f"{baseline_cfg['window']['start'][:4]}-{baseline_cfg['window']['end'][:4]}",
                    "demo baseline", h, json.dumps(snapshot(data_conn, baseline_cfg))))
    prev = db.execute("SELECT * FROM snapshots WHERE kind = ? ORDER BY id DESC LIMIT 1", (kind,)).fetchone()

    snap = snapshot(data_conn, cfg)
    window = f"{cfg['window']['start'][:4]}-{cfg['window']['end'][:4]}"
    sid = db.execute("INSERT INTO snapshots (created_at, kind, window, window_note, config_hash, data_json) VALUES (?,?,?,?,?,?)",
                     (now, kind, window, note, h, json.dumps(snap))).lastrowid
    result = {"window": window, "window_note": note, "snapshot_id": sid, "alerts": []}
    if prev is None:
        result["note"] = "first snapshot: baseline saved, nothing to compare yet"
    else:
        found = compare(json.loads(prev["data_json"]), snap, load_scoring_config()["alerts"], prev["config_hash"], h)
        alerts = [{"hub_id": hub, "hub": snap[hub]["name"], "reasons": r,
                   "overall": [json.loads(prev["data_json"])[hub]["overall"], snap[hub]["overall"]]}
                  for hub, r in found.items()]
        result.update(alerts=alerts, compared_with=f"snapshot {prev['id']} ({prev['window']})")
        if alerts:
            result["delivery"] = deliver({"source": "weather-risk-agent", "kind": kind, "window": window,
                                          "previous_window": prev["window"], "alerts": alerts})
        for a in alerts:
            db.execute("INSERT INTO alerts (created_at, snapshot_id, prev_snapshot_id, hub_id, reasons_json, delivery) "
                       "VALUES (?,?,?,?,?,?)", (now, sid, prev["id"], a["hub_id"], json.dumps(a["reasons"]),
                                                result.get("delivery")))
    db.commit()
    db.close()
    return result


def run_demo() -> dict:
    """Real change, real data: score 2015-2024 as the baseline, then 2016-2025."""
    cfg = load_scoring_config()
    end = int(cfg["window"]["end"][:4])
    return run(kind="demo", refresh_fema=False, cfg_override=shift_window(cfg, end),
               baseline_cfg=shift_window(cfg, end - 1))


def recent_alerts(limit: int = 50) -> list[dict]:
    db = _db()
    rows = db.execute("SELECT a.*, s.window, s.kind FROM alerts a JOIN snapshots s ON s.id = a.snapshot_id "
                      "ORDER BY a.id DESC LIMIT ?", (limit,)).fetchall()
    db.close()
    return [{**dict(r), "reasons": json.loads(r["reasons_json"])} for r in rows]


if __name__ == "__main__":
    res = run_demo() if "--demo" in sys.argv else run()
    print(f"{res['window']}  ({res['window_note']})  {res.get('compared_with', res.get('note', ''))}")
    for a in res["alerts"]:
        print(f"  ALERT {a['hub']}: " + " | ".join(a["reasons"]))
    print(f"{len(res['alerts'])} alert(s). delivery: {res.get('delivery', '-')}")
