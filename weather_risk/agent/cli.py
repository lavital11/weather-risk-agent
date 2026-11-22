"""Terminal chat for testing the agent without the API/UI.

Run:  .venv\\Scripts\\python.exe -m weather_risk.agent.cli            (interactive)
      .venv\\Scripts\\python.exe -m weather_risk.agent.cli "question" (one question)
Add --debug to print the plan JSON for each turn.
"""

import sys

from weather_risk.agent.agent import Agent, Turn


def main() -> None:
    debug = "--debug" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--debug"]
    agent, history = Agent(), []
    questions = iter(args) if args else iter(lambda: input("\nyou> "), "exit")
    for q in questions:
        reply = agent.ask(q, history)
        if debug and reply.plan:
            print(f"[plan] {reply.plan.model_dump_json(exclude_none=True)}")
        print(f"\nagent> {reply.answer.answer}")
        if reply.answer.caveats:
            print("  caveats: " + " | ".join(reply.answer.caveats))
        print(f"  [status={reply.status}, confidence={reply.answer.confidence}]")
        history.append(Turn(question=q, plan=reply.plan, answer=reply.answer.answer))


if __name__ == "__main__":
    main()
