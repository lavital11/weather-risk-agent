"""Executor tests: hand-written plans (no LLM) against the stored data."""

import pytest

from weather_risk.agent.executor import Executor, PlanError, competition_rank
from weather_risk.agent.schemas import QueryPlan
from weather_risk.config import DB_PATH

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="run the ingest first")


@pytest.fixture(scope="module")
def ex():
    return Executor()


def plan(**kw) -> QueryPlan:
    return QueryPlan(restated_question="test", **kw)


def test_competition_rank_ties():
    assert competition_rank({"a": 5, "b": 3, "c": 3, "d": 1}) == {"a": 1, "b": 2, "c": 2, "d": 4}


def test_denver_2025_snow_percentage_matches_v6(ex):
    f = ex.run(plan(intent="weather_stat", hubs=["denver"], metric="snow_days", year=2025))
    assert f["count_days"] == 38 and f["days_in_period"] == 365
    assert f["percent_of_days"] == 10.4


def test_midwest_winter_ranking_only_midwest_and_ordered(ex):
    f = ex.run(plan(intent="rank", region="Midwest", hazards=["winter"]))
    assert {r["hub_id"] for r in f["ranking"]} == {"chicago", "minneapolis", "indianapolis", "kansas_city", "columbus"}
    scores = [r["score"] for r in f["ranking"]]
    assert scores == sorted(scores, reverse=True)
    assert f["ranking"][0]["hub_id"] == "minneapolis"


def test_dallas_premise_check_is_nuanced(ex):
    f = ex.run(plan(intent="explain", hubs=["dallas"], asserted_level="high"))
    assert f["premise_check"]["premise_holds"] is False
    assert f["premise_check"]["rank"] == 6
    notable = {(n["signal"].split(" (")[0].split(" within")[0], n["rank_among_hubs"]) for n in f["notable_signals"]}
    assert any("hail >= 1 in" in s and r == 1 for s, r in notable)      # #1 for hail
    assert any("ice" in s.lower() and r == 2 for s, r in notable)       # #2 for ice (tied)


def test_zero_scores_get_no_rank_and_ties_are_flagged(ex):
    f = ex.run(plan(intent="explain", hubs=["dallas"]))
    hurricane = f["hazards"]["hurricane"]
    assert hurricane["score"] == 0 and hurricane["rank"] is None      # not "rank 3"
    severe = ex.run(plan(intent="explain", hubs=["chicago"]))["hazards"]["severe_storm"]["signals"]
    fema = next(s for s in severe if s["signal"].startswith("FEMA"))
    assert fema["rank_among_hubs"] == 1 and fema["tied"] is True        # tied with Kansas City


def test_houston_saturates_and_other_hazards_contribute_nothing(ex):
    f = ex.run(plan(intent="explain", hubs=["houston"]))
    assert f["overall_score"] == 100.0
    c = f["hazard_contributions_to_overall"]
    assert c["hurricane"] > 0                                        # removing hurricane lowers the score
    assert c["flood"] == 0.0 and c["winter"] == 0.0                  # saturated: removing them changes nothing


def test_compare_includes_hurricane_small_sample_note(ex):
    f = ex.run(plan(intent="compare", hubs=["miami", "houston"], hazards=["hurricane", "flood"]))
    assert [h["hub_id"] for h in f["hubs"]] == ["miami", "houston"]
    assert f["hubs"][0]["hazards"]["hurricane"]["notes"]


def test_invalid_plans_raise(ex):
    with pytest.raises(PlanError):
        ex.run(plan(intent="compare", hubs=["miami"]))
    with pytest.raises(PlanError):
        ex.run(plan(intent="weather_stat", hubs=["denver"]))   # no metric
