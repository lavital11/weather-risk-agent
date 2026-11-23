"""Convert the Claude Code session transcript (JSONL) into readable Markdown.

Run:  .venv\\Scripts\\python.exe scripts\\export_session.py <transcript.jsonl> docs\\session\\claude-code-session.md

What is included, in order:
  - every user message (pasted text kept; app-injected <system-reminder> blocks removed)
  - every assistant reply (text)
  - every tool call (name + input) with its result, in a collapsible <details> block
    (long inputs/outputs are truncated, with a note saying how much was cut)
Not included: the model's private reasoning blocks and app/telemetry records.

Secrets: the Gemini key from .env (if present) and anything that looks like an API key or a
password assignment are replaced with [REDACTED]; the author email with [email]. The script
prints how many of each it replaced.
"""

import json
import re
import sys
from pathlib import Path

MAX_INPUT = 2500     # characters of a tool input to keep
MAX_RESULT = 2500    # characters of a tool result to keep

REMINDER = re.compile(r"<system-reminder[^>]*>.*?</system-reminder[^>]*>", re.S)
PASTED = re.compile(r"</?pasted_content[^>]*>")
KEY_LIKE = re.compile(r"AIza[0-9A-Za-z_\-]{20,}")
PW_ASSIGN = re.compile(r"((?:DEMO_PASSWORD|GEMINI_API_KEY|GOOGLE_API_KEY)\s*=\s*)(\"?)([^\s\"';]{4,})")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@gmail\.com")
counts = {"gemini_key": 0, "key_like": 0, "assignments": 0, "emails": 0}


def load_secret_values() -> list[str]:
    """Values from .env that must never appear (read here, never printed)."""
    env = Path(".env")
    vals = []
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if len(v) >= 8:
                    vals.append(v)
    return vals


# Plus the throwaway password used once in a local Render rehearsal (never used on Render):
# redacted anyway so no password-like value appears in the published transcript.
SECRETS = load_secret_values() + ["rehearsal-only"]


def scrub(text: str) -> str:
    for v in SECRETS:
        if v in text:
            counts["gemini_key"] += text.count(v)
            text = text.replace(v, "[REDACTED]")
    text, n = KEY_LIKE.subn("[REDACTED]", text); counts["key_like"] += n

    def assign(m):
        if m.group(3).startswith(("$", "your-", "[REDACTED", "os.", "<")) or m.group(3) in ("None",):
            return m.group(0)
        counts["assignments"] += 1
        return m.group(1) + m.group(2) + "[REDACTED]"
    text = PW_ASSIGN.sub(assign, text)
    text, n = EMAIL.subn("[email]", text); counts["emails"] += n
    return text


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [truncated: {len(text) - limit:,} more characters]"


def fence(text: str) -> str:
    # pick a fence longer than any backtick run inside the text
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    f = "`" * max(3, longest + 1)
    return f"{f}\n{text}\n{f}"


def user_text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


def result_text(block) -> str:
    c = block.get("content")
    if isinstance(c, list):
        parts = []
        for b in c:
            if b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif b.get("type") == "image":
                parts.append("[image]")
        return "\n".join(parts)
    return str(c or "")


def tool_input_text(name: str, inp: dict) -> tuple[str, str]:
    """(one-line summary, body) for a tool call."""
    desc = inp.get("description") or inp.get("action_summary") or ""
    if name in ("PowerShell", "Bash"):
        return desc, inp.get("command", "")
    if name == "Write":
        return inp.get("file_path", ""), inp.get("content", "")
    if name == "Edit":
        return inp.get("file_path", ""), f"--- old\n{inp.get('old_string', '')}\n--- new\n{inp.get('new_string', '')}"
    if name == "Read":
        return inp.get("file_path", ""), json.dumps(inp, ensure_ascii=False)
    return desc, json.dumps(inp, ensure_ascii=False, indent=1)


def convert(src: Path, dst: Path) -> None:
    rows = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    results = {}
    for r in rows:
        if r.get("type") == "user" and isinstance(r.get("message", {}).get("content"), list):
            for b in r["message"]["content"]:
                if b.get("type") == "tool_result":
                    results[b.get("tool_use_id")] = result_text(b)

    out = [
        "# Claude Code session: Weather Risk Intelligence Agent",
        "",
        "Full working session with the AI agent (Claude Code, desktop app), exported from the local",
        f"transcript `{src.name}` with `scripts/export_session.py`.",
        "",
        "- **Included:** every user message, every assistant reply, and every tool call with its result",
        "  (commands, file edits, API checks), in order. Tool calls are collapsed; long inputs/outputs are truncated.",
        "- **Not included:** the model's private reasoning blocks, app-injected reminders, telemetry records.",
        "- **Redacted:** API keys and password values (`[REDACTED]`), email addresses (`[email]`).",
        "- A first attempt at 10:13 UTC on 2026-11-22 was interrupted before any reply; the session restarted",
        "  at 10:17 with the same first message. Times below are UTC.",
        "- The plan and every decision are also summarized in [../PLAN.md](../PLAN.md).",
        "",
        "---",
        "",
    ]
    n_user = n_tools = 0
    for r in rows:
        t = r.get("type")
        if t not in ("user", "assistant"):
            continue
        ts = (r.get("timestamp") or "")[:16].replace("T", " ")
        content = r.get("message", {}).get("content")
        if t == "user":
            if isinstance(content, list) and any(b.get("type") == "tool_result" for b in content):
                continue                                   # shown with its tool call
            text = PASTED.sub("", REMINDER.sub("", user_text(content))).strip()
            if not text or text.startswith("<task-notification") or text.startswith("<local-command"):
                continue
            n_user += 1
            out += [f"## User · {ts}", "", scrub(text), ""]
            continue
        for b in content or []:
            bt = b.get("type")
            if bt == "text" and b.get("text", "").strip():
                out += ["**Claude:**", "", scrub(b["text"].strip()), ""]
            elif bt == "tool_use":
                n_tools += 1
                name = b.get("name", "")
                short = name.replace("mcp__", "").split("__")[-1]
                summary, body = tool_input_text(name, b.get("input", {}))
                res = results.get(b.get("id"), "(no result recorded)")
                out += [
                    f"<details><summary>Tool: <code>{short}</code> {scrub(summary)[:120]}</summary>",
                    "",
                    fence(scrub(clip(body, MAX_INPUT))),
                    "",
                    "Result:",
                    "",
                    fence(scrub(clip(res, MAX_RESULT))),
                    "",
                    "</details>",
                    "",
                ]
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(out), encoding="utf-8")
    print(f"wrote {dst} ({dst.stat().st_size / 1e6:.1f} MB): {n_user} user messages, {n_tools} tool calls")
    print("redactions:", counts)


if __name__ == "__main__":
    convert(Path(sys.argv[1]), Path(sys.argv[2]))
