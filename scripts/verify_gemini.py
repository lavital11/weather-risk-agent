"""Phase 0 check (V4): confirm the Gemini features this project depends on.

Run:  .venv\\Scripts\\python.exe scripts\\verify_gemini.py
Needs the GEMINI_API_KEY environment variable. GEMINI_MODEL is optional.

Four checks, each prints PASS/FAIL. One failing check does not stop the others:
  1. The model name exists for this API key.
  2. Structured output: the reply matches a Pydantic schema.
  3. Function calling: the model returns a tool call instead of text.
  4. Tools + forced JSON in the SAME request. This only matters for the
     agent-design choice (D4). If it fails, D4 option A needs two calls per turn.
"""

import os
import sys
from typing import Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

MODEL = os.environ.get("GEMINI_MODEL", "gemini-4-hydrogen")


# A tiny version of the "QueryPlan" contract we would use in the real agent.
class MiniPlan(BaseModel):
    intent: Literal["rank", "compare", "weather_stat", "explain", "out_of_scope"]
    hubs: list[str]
    hazard: Literal["winter", "hurricane", "flood", "severe_storm", "none"]


def report(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))


def check_model_exists(client: genai.Client) -> None:
    names = [m.name for m in client.models.list()]  # e.g. "models/gemini-..."
    ok = any(n.endswith("/" + MODEL) or n == MODEL for n in names)
    report("1. model exists", ok, MODEL if ok else f"not found; available: {sorted(names)}")


def check_structured_output(client: genai.Client) -> None:
    resp = client.models.generate_content(
        model=MODEL,
        contents="Compare Miami and Houston in terms of hurricane exposure.",
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=MiniPlan,  # SDK turns the Pydantic class into a schema
            temperature=0,
        ),
    )
    # We validate the raw text ourselves and don't rely on resp.parsed:
    # the real code will do the same, so we test that path.
    try:
        plan = MiniPlan.model_validate_json(resp.text)
        report("2. structured output", True, plan.model_dump_json())
    except ValidationError as e:
        report("2. structured output", False, f"{e}\nraw: {resp.text!r}")


# Tool declaration: only a description of the function. Gemini never runs our code.
RANK_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="rank_hubs",
            description="Rank logistics hubs by weather exposure score.",
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "region": {"type": "string", "description": "e.g. Midwest"},
                    "hazard": {"type": "string", "enum": ["winter", "hurricane", "flood", "severe_storm"]},
                },
                "required": ["hazard"],
            },
        )
    ]
)
NO_AUTO_CALL = types.AutomaticFunctionCallingConfig(disable=True)


def check_function_calling(client: genai.Client) -> None:
    resp = client.models.generate_content(
        model=MODEL,
        contents="Which hubs in the Midwest are most exposed to winter disruption?",
        config=types.GenerateContentConfig(
            tools=[RANK_TOOL], automatic_function_calling=NO_AUTO_CALL, temperature=0
        ),
    )
    calls = resp.function_calls or []
    if calls:
        report("3. function calling", True, f"{calls[0].name}({dict(calls[0].args or {})})")
    else:
        report("3. function calling", False, f"no tool call; text: {resp.text!r}")


def check_tools_plus_json(client: genai.Client) -> None:
    try:
        resp = client.models.generate_content(
            model=MODEL,
            contents="Which hubs in the Midwest are most exposed to winter disruption?",
            config=types.GenerateContentConfig(
                tools=[RANK_TOOL],
                automatic_function_calling=NO_AUTO_CALL,
                response_mime_type="application/json",
                response_schema=MiniPlan,
                temperature=0,
            ),
        )
        kind = "tool call" if resp.function_calls else f"text {resp.text!r}"
        report("4. tools + JSON schema in one call", True, f"accepted; model returned {kind}")
    except Exception as e:  # API rejects the combination -> we learn that here
        report("4. tools + JSON schema in one call", False, f"{type(e).__name__}: {e}")


def main() -> None:
    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("Set GEMINI_API_KEY first.")
    client = genai.Client()  # reads GEMINI_API_KEY from the environment
    for check in (check_model_exists, check_structured_output, check_function_calling, check_tools_plus_json):
        try:
            check(client)
        except Exception as e:
            report(check.__name__, False, f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
