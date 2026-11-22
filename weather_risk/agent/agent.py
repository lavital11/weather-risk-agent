"""The agent loop for one user turn:

    question + history --LLM--> QueryPlan --code--> facts --LLM--> Answer

Fallbacks (no crash, no made-up numbers):
- LLM returns invalid JSON twice      -> "please rephrase" reply
- plan valid but not executable       -> ask for the missing detail
- out_of_scope / clarify plans        -> deterministic reply, no second LLM call
"""

import json

from pydantic import BaseModel

from weather_risk.agent.executor import Executor, PlanError
from weather_risk.agent.prompts import NARRATOR_SYSTEM, planner_system
from weather_risk.agent.schemas import Answer, QueryPlan
from weather_risk.llm import LLMClient, LLMOutputError

HISTORY_TURNS = 6   # how many previous turns the planner sees


class Turn(BaseModel):
    question: str
    plan: QueryPlan | None = None
    answer: str


class AgentReply(BaseModel):
    answer: Answer
    plan: QueryPlan | None
    facts: dict | None
    status: str            # ok | out_of_scope | clarify | plan_error | llm_error


def _history_text(history: list[Turn]) -> str:
    if not history:
        return "(no previous turns)"
    lines = []
    for t in history[-HISTORY_TURNS:]:
        plan = t.plan.model_dump_json(exclude_none=True) if t.plan else "null"
        lines.append(f"User: {t.question}\nPlan: {plan}\nAssistant: {t.answer[:400]}")
    return "\n\n".join(lines)


class Agent:
    def __init__(self, llm: LLMClient | None = None, executor: Executor | None = None):
        self.llm = llm or LLMClient()
        self.executor = executor or Executor()
        self.planner_system = planner_system()

    def plan(self, question: str, history: list[Turn]) -> QueryPlan:
        prompt = f"Conversation so far:\n{_history_text(history)}\n\nNew question: {question}"
        return self.llm.generate_json(self.planner_system, prompt, QueryPlan)

    def ask(self, question: str, history: list[Turn] | None = None) -> AgentReply:
        history = history or []
        try:
            plan = self.plan(question, history)
        except LLMOutputError:
            return _fixed("Sorry, I couldn't interpret that. Could you rephrase?", None, "llm_error")

        if plan.intent == "out_of_scope":
            hazards = ", ".join(hz["label"].split(" (")[0].lower() for hz in self.executor.cfg["hazards"].values())
            return _fixed(f"That's outside what I cover: {plan.out_of_scope_reason or 'not in scope'}. "
                          f"I cover {self.executor.n_hubs} hubs and these hazards: {hazards}.", plan, "out_of_scope")
        if plan.intent == "clarify":
            return _fixed(plan.clarification_question or "Could you clarify your question?", plan, "clarify")

        try:
            facts = self.executor.run(plan)
        except PlanError as e:
            return _fixed(f"I need a bit more detail: {e}.", plan, "plan_error")

        prompt = (f"Question: {plan.restated_question}\n\nPlan: {plan.model_dump_json(exclude_none=True)}\n\n"
                  f"FACTS:\n{json.dumps(facts, indent=1)}")
        try:
            answer = self.llm.generate_json(NARRATOR_SYSTEM, prompt, Answer, temperature=0.2)
        except LLMOutputError:
            return AgentReply(answer=Answer(answer="I computed the facts but couldn't write the answer. Raw facts attached.",
                                            confidence="low"), plan=plan, facts=facts, status="llm_error")
        # Follow-up transparency: say what was carried over from earlier turns. Done in code (not left
        # to the narrator) so it always appears; only when there IS history, so a first question never gets it.
        if history and plan.carried_over:
            answer.answer = f"Assuming you still mean {plan.carried_over.strip().rstrip('.')}:\n{answer.answer}"
        return AgentReply(answer=answer, plan=plan, facts=facts, status="ok")


def _fixed(text: str, plan: QueryPlan | None, status: str) -> AgentReply:
    return AgentReply(answer=Answer(answer=text, confidence="high"), plan=plan, facts=None, status=status)
