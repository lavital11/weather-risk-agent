"""Chat history per session, in its own SQLite file (data/sessions.db, git-ignored).

Kept separate from weather_risk.db so the committed data snapshot never
contains chat logs. One row per turn; the plan is stored so follow-ups can be
resolved ("what about Chicago?" reuses the previous intent and hazards).
"""

import sqlite3
import uuid
from datetime import datetime, timezone

from weather_risk.agent.agent import Turn
from weather_risk.agent.schemas import QueryPlan
from weather_risk.config import ROOT

SESSIONS_DB = ROOT / "data" / "sessions.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_turns (
    session_id TEXT NOT NULL,
    turn_no    INTEGER NOT NULL,
    question   TEXT NOT NULL,
    plan_json  TEXT,
    answer     TEXT NOT NULL,
    status     TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, turn_no)
);
"""


def _conn() -> sqlite3.Connection:
    # A new short-lived connection per call: simple and thread-safe for the API.
    conn = sqlite3.connect(SESSIONS_DB)
    conn.executescript(SCHEMA)
    return conn


def new_session_id() -> str:
    return uuid.uuid4().hex


def load_history(session_id: str) -> list[Turn]:
    with _conn() as conn:
        rows = conn.execute("SELECT question, plan_json, answer FROM chat_turns WHERE session_id = ? "
                            "ORDER BY turn_no", (session_id,)).fetchall()
    return [Turn(question=q, plan=QueryPlan.model_validate_json(p) if p else None, answer=a) for q, p, a in rows]


def save_turn(session_id: str, question: str, plan: QueryPlan | None, answer: str, status: str) -> None:
    with _conn() as conn:  # "with" commits on success
        n = conn.execute("SELECT COUNT(*) FROM chat_turns WHERE session_id = ?", (session_id,)).fetchone()[0]
        conn.execute("INSERT INTO chat_turns VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (session_id, n + 1, question, plan.model_dump_json() if plan else None, answer, status,
                      datetime.now(timezone.utc).isoformat(timespec="seconds")))
