"""Run the evaluation set against the live agent (Gemini + deterministic code).

Run:  .venv\\Scripts\\python.exe -m evals.run_eval              (all cases)
      .venv\\Scripts\\python.exe -m evals.run_eval dallas_premise  (some cases, by id)

What is checked per case (see evals/cases.toml):
  plan       did the LLM understand the question?  (expected QueryPlan fields)
  facts      is the deterministic result right?     (e.g. top hub, premise_holds)
  answer     does the reply say the key things / avoid forbidden things / include caveats?
  grounding  is every number in the reply present in the facts? (catches invented numbers)
Writes evals/results/latest.json. ~2 LLM calls per question.
"""

import json
import re
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from weather_risk.agent.agent import Agent, AgentReply, Turn

HERE = Path(__file__).resolve().parent
# A number not preceded by a letter/digit/dot. A '-' right after a letter is a hyphen
# ("top-3"), not a minus sign: an earlier version read it as -3 (grader bug, run 1).
NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


# ------------------------------------------------------------------ individual checks

def check_plan(expected: dict, reply: AgentReply) -> list[str]:
    if reply.plan is None:
        return ["no plan"]
    got, errors = reply.plan.model_dump(), []
    for field, want in expected.items():
        have = got.get(field)
        if isinstance(want, list):
            ok = set(want) == set(have or [])
        else:
            ok = have == want
        if not ok:
            errors.append(f"plan.{field}: expected {want!r}, got {have!r}")
    return errors


def check_facts(expected: dict, facts: dict | None) -> list[str]:
    if facts is None:
        return ["no facts"] if expected else []
    getters = {
        "top_hub": lambda f: f["ranking"][0]["hub_id"],
        "premise_holds": lambda f: f["premise_check"]["premise_holds"],
        "percent_of_days": lambda f: f["percent_of_days"],
        "partial_year": lambda f: f["partial_year"],
        "overall_score": lambda f: f["overall_score"],
    }
    errors = []
    for key, want in expected.items():
        try:
            have = getters[key](facts)
        except (KeyError, IndexError, TypeError):
            have = "<missing>"
        if have != want:
            errors.append(f"facts.{key}: expected {want!r}, got {have!r}")
    return errors


def check_answer(case: dict, reply: AgentReply) -> list[str]:
    text = reply.answer.answer.lower()
    errors = [f"missing one of {group}" for group in case.get("must_mention", [])
              if not any(alt.lower() in text for alt in group)]
    errors += [f"forbidden phrase {p!r}" for p in case.get("must_not_mention", []) if p.lower() in text]
    if "caveat_mentions" in case:
        cav = " ".join(reply.answer.caveats).lower() + " " + text
        if not any(c.lower() in cav for c in case["caveat_mentions"]):
            errors.append(f"no caveat mentioning {case['caveat_mentions']}")
    return errors


def numbers_in(obj) -> set[float]:
    """All numbers anywhere in the facts, including inside labels like '>= 50 mm'."""
    out: set[float] = set()
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, str):
        out.update(float(n) for n in NUM.findall(DATE.sub(" ", obj)))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out |= numbers_in(k) | numbers_in(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= numbers_in(v)
    return out


def check_grounding(reply: AgentReply, question: str) -> list[str]:
    """Every number in the answer must come from the facts (rounding allowed).

    Allowed without a match: small integers 0-10 (ranks, "of 10"), years 2016-2026,
    numbers in the question. Dates are checked as whole strings.
    """
    if reply.facts is None:
        return []
    answer = reply.answer.answer
    facts_text = json.dumps(reply.facts)
    errors = [f"date {d} not in facts" for d in DATE.findall(answer) if d not in facts_text]
    known = numbers_in(reply.facts) | {float(n) for n in NUM.findall(question)}
    for raw in NUM.findall(DATE.sub(" ", answer)):
        x = float(raw)
        if (x.is_integer() and 0 <= x <= 10) or 2016 <= x <= 2026:
            continue
        if not any(abs(x - f) <= 0.051 or round(f) == x for f in known):
            errors.append(f"ungrounded number {raw}")
    return errors


# ------------------------------------------------------------------ runner

def run_case(agent: Agent, case: dict) -> dict:
    history: list[Turn] = []
    for q in case.get("history", []):                      # build the conversation first
        r = agent.ask(q, history)
        history.append(Turn(question=q, plan=r.plan, answer=r.answer.answer))
    t0 = time.time()
    reply = agent.ask(case["question"], history)
    result = {
        "id": case["id"], "question": case["question"], "seconds": round(time.time() - t0, 1),
        "status": reply.status,
        "checks": {
            "status": [] if reply.status == case.get("status", "ok") else
                      [f"status: expected {case.get('status', 'ok')}, got {reply.status}"],
            "plan": check_plan(case.get("plan", {}), reply),
            "facts": check_facts(case.get("facts", {}), reply.facts),
            "answer": check_answer(case, reply),
            "grounding": check_grounding(reply, case["question"]),
        },
        "plan": reply.plan.model_dump(exclude_none=True) if reply.plan else None,
        "answer": reply.answer.answer, "caveats": reply.answer.caveats,
    }
    result["passed"] = not any(result["checks"].values())
    return result


def run_once(agent: Agent, cases: list[dict], label: str) -> list[dict]:
    print(f"\n=== {label} ===")
    results = []
    for c in cases:
        try:
            r = run_case(agent, c)
        except Exception as e:   # one broken case shouldn't stop the run
            r = {"id": c["id"], "question": c["question"], "passed": False,
                 "checks": {"error": [f"{type(e).__name__}: {e}"]}}
        r["holdout"] = c.get("holdout", False)
        results.append(r)
        mark = "PASS" if r["passed"] else "FAIL"
        cols = "  ".join(f"{k}:{'ok' if not v else 'X'}" for k, v in r["checks"].items())
        print(f"[{mark}] {c['id']:<30} {cols}")
        for k, v in r["checks"].items():
            for msg in v:
                print(f"         - {k}: {msg}")
    print(f"{sum(r['passed'] for r in results)}/{len(results)} passed")
    return results


def main() -> None:
    # Usage: run_eval [--repeat N] [case_id ...]
    args = sys.argv[1:]
    repeat = 1
    if "--repeat" in args:
        i = args.index("--repeat")
        repeat = int(args[i + 1])
        args = args[:i] + args[i + 2:]
    cases = tomllib.loads((HERE / "cases.toml").read_text(encoding="utf-8"))["cases"]
    if args:
        cases = [c for c in cases if c["id"] in args]
    agent = Agent()
    runs = [run_once(agent, cases, f"run {n}/{repeat}") for n in range(1, repeat + 1)]

    # Per-case pass rate across runs: the LLM is not fully deterministic, so one run is one sample.
    per_case = {c["id"]: sum(run[i]["passed"] for run in runs) for i, c in enumerate(cases)}
    print(f"\nPer-case pass rate over {repeat} run(s):")
    for c in cases:
        tag = " (holdout)" if c.get("holdout") else ""
        print(f"  {per_case[c['id']]}/{repeat}  {c['id']}{tag}")
    total = sum(per_case.values())
    print(f"Overall: {total}/{len(cases) * repeat} case-runs passed")

    results = runs[-1]
    out = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "model": agent.llm.model, "repeat": repeat,
           "per_case_passes": per_case, "case_runs_passed": total, "case_runs_total": len(cases) * repeat,
           "runs": runs}
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "latest.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("Wrote evals/results/latest.json")


if __name__ == "__main__":
    main()
