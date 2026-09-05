"""Gate 1 proof: Scout answers "which shots are at risk and why" over real MCP.

    ./.venv/Scripts/python.exe scripts/run_scout.py

Prints Scout's briefing and the full tool-call log, and leaves a journal behind
in journals/ as the record.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from agent import journal as J  # noqa: E402
from agent.crew.scout import build_scout  # noqa: E402
from agent.runtime import aclose, run_agent  # noqa: E402

QUESTION = (
    "Sweep the render farm now. Which shots are at risk of missing the "
    "delivery date, and what is the farm doing that puts them there?"
)


async def main() -> int:
    jnl = J.Journal()
    print(f"journal: {jnl.path.name}\n")
    agent = build_scout()
    try:
        answer = await run_agent(agent, QUESTION, jnl, actor="scout")
    finally:
        await aclose(agent)
        jnl.close()

    print("=" * 72)
    print("SCOUT BRIEFING")
    print("=" * 72)
    print(answer or "(no final text)")

    print("\n" + "=" * 72)
    print("TOOL CALL LOG  (proof the agent actually queried Grafana)")
    print("=" * 72)
    calls = 0
    for event in jnl.events:
        if event.kind == J.TOOL_CALL:
            calls += 1
            args = event.payload.get("args", {})
            expr = args.get("expr") or args.get("query") or ""
            print(f"\n[{event.offset:6.1f}s] CALL  {event.payload['tool']}")
            for k, v in args.items():
                if k not in ("expr", "query"):
                    print(f"           {k}: {v}")
            if expr:
                print(f"           expr: {expr}")
        elif event.kind == J.TOOL_RESULT:
            r = event.payload.get("result", "")
            print(f"           -> {event.payload.get('latency_ms')}ms  {r[:220]}")

    print(f"\n{calls} Grafana MCP tool calls made.")
    return 0 if calls else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
