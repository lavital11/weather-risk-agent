"""Deterministic executor: QueryPlan in, facts (plain dict) out. No LLM here.

Every number the agent says comes from this file or the scoring engine.
The facts are also what the eval checks against.
"""

import sqlite3
import threading

from weather_risk.agent.metrics import METRICS
from weather_risk.agent.schemas import QueryPlan
from weather_risk.config import load_hubs
from weather_risk.db import connect
from weather_risk.scoring import HubScore, combine_hazards, distance_km, load_scoring_config, score_all

HIGH_RANK_MAX = 3      # premise "high" holds if the hub is in the top 3 of 10 (top 30%)
LOW_RANK_MIN = 8       # premise "low" holds if the hub is in the bottom 3
NOTABLE_RANK = 2       # signals where the hub ranks #1 or #2 are worth mentioning
TIE_GAP = 1.0          # overall scores closer than this are "effectively tied" (user decision):
                       # at the saturated top (e.g. 99.1 vs 98.8) the gap is noise, not a real difference


def effectively_tied(scores: dict[str, float], hub_id: str) -> list[str]:
    """Other hubs whose score is within TIE_GAP of this hub's score."""
    return sorted(h for h, s in scores.items() if h != hub_id and abs(s - scores[hub_id]) < TIE_GAP)
LAST_COMPLETE_YEAR = 2025


class PlanError(ValueError):
    """The plan is valid JSON but can't be executed (e.g. compare with one hub)."""


def competition_rank(values: dict[str, float]) -> dict[str, int]:
    """Highest value = rank 1; ties share a rank (1, 2, 2, 4)."""
    ordered = sorted(values.values(), reverse=True)
    return {k: ordered.index(v) + 1 for k, v in values.items()}


class Executor:
    def __init__(self, conn: sqlite3.Connection | None = None):
        self.conn = conn or connect(check_same_thread=False)
        self._lock = threading.Lock()
        self.cfg = load_scoring_config()
        self.hubs = {h.id: h for h in load_hubs()}
        self.scores: dict[str, HubScore] = {s.hub_id: s for s in score_all(self.conn, self.cfg)}
        self.n_hubs = len(self.scores)
        self._build_ranks()

    # ------------------------------------------------------------- ranks
    def _build_ranks(self) -> None:
        s = self.scores
        self.overall_rank = competition_rank({h: x.overall for h, x in s.items()})
        self.hazard_rank = {hz: competition_rank({h: x.hazards[hz].score for h, x in s.items()})
                            for hz in self.cfg["hazards"]}
        # Signal ranks use the raw value (e.g. hail days/yr), keyed "hazard.signal_id".
        self.signal_rank: dict[str, dict[str, int]] = {}
        any_hub = next(iter(s.values()))
        for hz, res in any_hub.hazards.items():
            for ci, comp in enumerate(res.components):
                for si, sig in enumerate(comp.signals):
                    key = f"{hz}.{sig.id}"
                    self.signal_rank[key] = competition_rank(
                        {h: x.hazards[hz].components[ci].signals[si].value for h, x in s.items()})

    def rank_info(self, ranks: dict[str, int], hub_id: str, value: float) -> dict:
        """Rank fields for the facts. A value of 0 gets no rank: "rank 3 of 10" for zero
        exposure (8 hubs tied at 0) misled the narrator in the first live run."""
        if value == 0:
            return {"rank_among_hubs": None, "rank_note": "zero; tied with all other hubs at zero"}
        tied = sum(1 for r in ranks.values() if r == ranks[hub_id]) > 1
        return {"rank_among_hubs": ranks[hub_id], "tied": tied}

    # ------------------------------------------------------------- building blocks
    def hazard_contribution(self, hub_id: str, hazard: str) -> float:
        """Points the overall score would lose if this hazard were 0 (leave-one-out).

        Works for any combine method. With noisy-OR it's the honest attribution:
        e.g. if one hazard is at 100 (overall saturated), removing any OTHER
        hazard changes nothing, so the others contribute 0.
        """
        sc = self.scores[hub_id]
        without = {h: (0.0 if h == hazard else r.score) for h, r in sc.hazards.items()}
        return round(sc.overall - combine_hazards(without, self.cfg), 1)

    def drivers(self, hub_id: str, hazard: str | None = None, top: int = 5) -> list[dict]:
        """Which signals contribute the most points: to one hazard score, or to the overall score.

        Within a hazard, points are additive: component weight * signal score / n signals.
        For the overall score, each hazard's leave-one-out contribution is split among its
        signals in proportion to their points within the hazard (an approximation, since
        noisy-OR isn't additive).
        """
        out = []
        for hz, res in self.scores[hub_id].hazards.items():
            if hazard and hz != hazard:
                continue
            scale_to_overall = 1.0 if hazard else (
                self.hazard_contribution(hub_id, hz) / res.score if res.score else 0.0)
            for comp in res.components:
                for sig in comp.signals:
                    points = scale_to_overall * comp.weight * sig.score / len(comp.signals)
                    out.append({"hazard": hz, "signal": sig.label, "value": sig.value, "unit": sig.unit,
                                "signal_score": sig.score, "points": round(points, 1),
                                **self.rank_info(self.signal_rank[f"{hz}.{sig.id}"], hub_id, sig.value)})
        return sorted(out, key=lambda d: -d["points"])[:top]

    def notable_signals(self, hub_id: str) -> list[dict]:
        """Signals where this hub is #1 or #2 of all hubs (value > 0)."""
        out = []
        for hz, res in self.scores[hub_id].hazards.items():
            for comp in res.components:
                for sig in comp.signals:
                    r = self.signal_rank[f"{hz}.{sig.id}"][hub_id]
                    if r <= NOTABLE_RANK and sig.value > 0:
                        out.append({"hazard": hz, "signal": sig.label, "value": sig.value, "unit": sig.unit,
                                    **self.rank_info(self.signal_rank[f"{hz}.{sig.id}"], hub_id, sig.value)})
        return out

    def hub_summary(self, hub_id: str, hazards: list[str]) -> dict:
        sc = self.scores[hub_id]
        hz_list = hazards or list(sc.hazards)
        return {
            "hub": sc.hub_name, "hub_id": hub_id, "region": sc.region,
            "overall_score": sc.overall, "overall_rank": self.overall_rank[hub_id],
            "overall_effectively_tied_with": [self.scores[h].hub_name for h in
                                              effectively_tied({h: x.overall for h, x in self.scores.items()}, hub_id)],
            "hazards": {
                hz: {"score": sc.hazards[hz].score,
                     **{("rank" if k == "rank_among_hubs" else k): v
                        for k, v in self.rank_info(self.hazard_rank[hz], hub_id, sc.hazards[hz].score).items()},
                     "signals": [{"signal": s.label, "value": s.value, "unit": s.unit, "score": s.score,
                                  "bound": s.bound, **self.rank_info(self.signal_rank[f"{hz}.{s.id}"], hub_id, s.value)}
                                 for c in sc.hazards[hz].components for s in c.signals],
                     "fema_events": [e.model_dump() for c in sc.hazards[hz].components for e in c.events],
                     "storm_passes": [s.model_dump() for c in sc.hazards[hz].components for s in c.storms],
                     "notes": sc.hazards[hz].notes}
                for hz in hz_list},
        }

    def _common(self) -> dict:
        w = self.cfg["window"]
        method = self.cfg["overall"]["method"]
        how = ("noisy-OR: overall = 100 * (1 - product of (1 - hazard/100)); disruption if ANY hazard hits, "
               "so a low hazard can't offset a high one; one hazard at 100 makes the overall 100"
               if method == "noisy_or" else f"weighted average, weights {self.cfg['hazard_weights']}")
        return {"scoring_window": f"{w['start']} to {w['end']}", "n_hubs": self.n_hubs,
                "score_scale": "0-100, absolute (fixed bounds in config), higher = more exposed",
                "overall_method": how}

    # ------------------------------------------------------------- intents
    def run(self, plan: QueryPlan) -> dict:
        handler = {"rank": self.rank, "compare": self.compare,
                   "explain": self.explain, "weather_stat": self.weather_stat}.get(plan.intent)
        if handler is None:   # out_of_scope / clarify need no data
            return {"intent": plan.intent}
        with self._lock:      # one shared SQLite connection; API requests run in threads
            return {"intent": plan.intent, **self._common(), **handler(plan)}

    def rank(self, plan: QueryPlan) -> dict:
        ids = [h for h in self.scores if plan.region is None or self.hubs[h].region == plan.region]
        if plan.hubs:                                  # "rank these hubs"
            ids = [h for h in ids if h in plan.hubs]

        def key(h):  # one hazard -> its score; several -> their average; none -> overall
            if not plan.hazards:
                return self.scores[h].overall
            return round(sum(self.scores[h].hazards[z].score for z in plan.hazards) / len(plan.hazards), 1)

        full = sorted(ids, key=key, reverse=True)
        considered = {h: key(h) for h in ids}
        n = plan.top_n or len(full)
        # Don't let top_n cut through a tie: "just the top 1" with Dallas 99.1 / Houston 98.8 must
        # return both, or the answer can't explain the tied hub (eval run 5).
        while n < len(full) and abs(considered[full[n]] - considered[full[n - 1]]) < TIE_GAP:
            n += 1
        ordered = full[:n]
        out = {
            "ranked_by": " + ".join(plan.hazards) if plan.hazards else "overall score",
            "region": plan.region, "hubs_considered": len(ids),
            "ranking": [{"position": i, "hub": self.scores[h].hub_name, "hub_id": h, "score": key(h),
                         "effectively_tied_with": effectively_tied(considered, h),
                         "overall_rank_of_all_hubs": self.overall_rank[h],
                         "top_drivers": self.drivers(h, plan.hazards[0] if len(plan.hazards) == 1 else None, top=3)}
                        for i, h in enumerate(ordered, 1)],
            "notes": sorted({n for h in ordered for z in (plan.hazards or self.cfg["hazards"])
                             for n in self.scores[h].hazards[z].notes}),
        }
        if len(ordered) > 1 and effectively_tied(considered, ordered[0]):
            tied = [ordered[0], *effectively_tied(considered, ordered[0])]
            out["top_is_effectively_tied"] = {
                "hubs": [self.scores[h].hub_name for h in tied],
                "scores": [considered[h] for h in tied],
                "rule": f"scores less than {TIE_GAP} point apart are effectively tied; do not name a single #1"}
        return out

    def compare(self, plan: QueryPlan) -> dict:
        if len(plan.hubs) < 2:
            raise PlanError("compare needs at least 2 hubs")
        return {"hubs": [self.hub_summary(h, plan.hazards) for h in plan.hubs]}

    def explain(self, plan: QueryPlan) -> dict:
        if len(plan.hubs) != 1:
            raise PlanError("explain needs exactly 1 hub")
        h = plan.hubs[0]
        hazard = plan.hazards[0] if len(plan.hazards) == 1 else None
        out = {**self.hub_summary(h, plan.hazards),
               "hazard_contributions_to_overall": {
                   z: self.hazard_contribution(h, z) for z in self.scores[h].hazards},
               "top_drivers": self.drivers(h, hazard),
               "notable_signals": self.notable_signals(h)}
        if plan.asserted_level:
            rank = self.hazard_rank[hazard][h] if hazard else self.overall_rank[h]
            holds = rank <= HIGH_RANK_MAX if plan.asserted_level == "high" else rank >= LOW_RANK_MIN
            out["premise_check"] = {
                "user_presumes": plan.asserted_level, "rank": rank, "of": self.n_hubs,
                "rule": f"'high' = top {HIGH_RANK_MAX}, 'low' = rank {LOW_RANK_MIN} or worse",
                "premise_holds": holds}
        return out

    def weather_stat(self, plan: QueryPlan) -> dict:
        if len(plan.hubs) != 1 or plan.metric is None:
            raise PlanError("weather_stat needs exactly 1 hub and a metric")
        hub, m = self.hubs[plan.hubs[0]], METRICS[plan.metric]
        year = plan.year or LAST_COMPLETE_YEAR
        start, end = f"{year}-01-01", f"{year}-12-31"
        n_days, last = self.conn.execute(
            "SELECT COUNT(*), MAX(date) FROM weather_daily WHERE hub_id = ? AND date BETWEEN ? AND ?",
            (hub.id, start, end)).fetchone()
        if not n_days:
            raise PlanError(f"no weather data for {year}")
        if plan.metric == "hail_days":
            rows = self.conn.execute("SELECT date, lat, lon FROM hail_reports WHERE date BETWEEN ? AND ? "
                                     "AND size_in >= 1.0", (start, end)).fetchall()
            count = len({d for d, la, lo in rows if distance_km(hub.lat, hub.lon, la, lo) <= 50})
            total = None
        else:
            count = self.conn.execute(f"SELECT COUNT(*) FROM weather_daily WHERE hub_id = ? AND date BETWEEN ? AND ? "
                                      f"AND {m.sql}", (hub.id, start, end)).fetchone()[0]
            total = (self.conn.execute(f"SELECT ROUND(SUM({m.total_column}), 1) FROM weather_daily "
                                       f"WHERE hub_id = ? AND date BETWEEN ? AND ?", (hub.id, start, end)).fetchone()[0]
                     if m.total_column else None)
        return {
            "hub": hub.name, "hub_id": hub.id, "point": f"{hub.airport} ({hub.lat}, {hub.lon})",
            "metric": m.label, "year": year, "days_in_period": n_days, "data_through": last,
            "partial_year": n_days < 365, "count_days": count,
            "percent_of_days": round(100 * count / n_days, 1),
            **({"period_total": total, "total_unit": m.unit} if total is not None else {}),
            "source": "NOAA SPC hail reports" if plan.metric == "hail_days" else "Open-Meteo archive (modeled reanalysis)",
        }
