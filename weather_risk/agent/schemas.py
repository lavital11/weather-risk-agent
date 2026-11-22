"""JSON contracts between the LLM and our code (requirement: enforced JSON schema).

QueryPlan: LLM -> code. What the user is asking, in fields our code can execute.
Answer:    LLM -> user. The explanation, written only from facts our code computed.

Allowed values (hub ids, hazards, regions, metrics) are built from the config
files, so the LLM literally cannot pick a hub or hazard that doesn't exist.
"""

from typing import Literal

from pydantic import BaseModel, Field

from weather_risk.agent.metrics import METRICS
from weather_risk.config import load_hubs
from weather_risk.scoring import load_scoring_config

_HUBS = load_hubs()
HubId = Literal[tuple(h.id for h in _HUBS)]                         # e.g. Literal['chicago', ...]
Region = Literal[tuple(sorted({h.region for h in _HUBS}))]
Hazard = Literal[tuple(load_scoring_config()["hazards"])]
MetricId = Literal[tuple(METRICS)]

Intent = Literal["rank", "compare", "weather_stat", "explain", "out_of_scope", "clarify"]


class QueryPlan(BaseModel):
    restated_question: str = Field(description="The user's question rewritten to stand alone, resolving follow-up references.")
    intent: Intent
    hubs: list[HubId] = Field(default_factory=list, description="Hubs named or implied. compare: 2+; explain/weather_stat: exactly 1.")
    region: Region | None = Field(default=None, description="Only for rank, when the user limits to a region.")
    hazards: list[Hazard] = Field(default_factory=list, description="Hazards asked about. Empty = overall score.")
    top_n: int | None = Field(default=None, description="rank: how many hubs to return, if the user says.")
    metric: MetricId | None = Field(default=None, description="weather_stat only.")
    year: int | None = Field(default=None, description="weather_stat: calendar year. 'last year' = 2025.")
    asserted_level: Literal["high", "low"] | None = Field(
        default=None, description="If the question presumes a hub's risk is high or low (e.g. 'why is X high?').")
    out_of_scope_reason: str | None = None
    clarification_question: str | None = None


class Answer(BaseModel):
    answer: str = Field(description="Reply to the user, plain language, using only numbers present in the facts.")
    caveats: list[str] = Field(default_factory=list, description="Data limits relevant to THIS answer (small samples, proxies, data bias).")
    confidence: Literal["high", "medium", "low"]
    hubs_mentioned: list[HubId] = Field(default_factory=list)
