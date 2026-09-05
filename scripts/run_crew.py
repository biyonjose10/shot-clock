"""Run one crew member against live telemetry and print its tool-call log.

    ./.venv/Scripts/python.exe scripts/run_crew.py --agent gaffer
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from agent import journal as J  # noqa: E402
from agent.runtime import aclose, run_agent  # noqa: E402

PROMPTS = {
    "scout": "Sweep the render farm now. Which shots are at risk of missing the "
             "delivery date, and what is the farm doing that puts them there?",
    "gaffer": "Frame times and delivery are slipping on the farm right now. "
              "Establish the cause from the telemetry and the logs, name the "
              "resource or node responsible, and say what you ruled out.",
    "producer": "The farm is degraded. Read the throughput and exposure from "
                "Prometheus and price what this does to the 30 September "
                "delivery date.",
    "first_ad": (
        "Record this investigation in Grafana and write the production note. "
        "CAUSE: licence starvation, both renderer pools at 0 of 12 seats free, "
        "34 renders queued and waiting. "
        "EVIDENCE: Loki line '[arnold] WARNING | licence checkout failed for "
        "arnold_render, 0 of 12 seats free, 35 renders queued'. Texture cache "
        "healthy at 0.9179 and node memory flat, so cache collapse and OOM were "
        "ruled out. "
        "IMPACT: 34 of 1200 shots projected past 30 September, delivery slips "
        "17 hours. "
        "COST: $88,514 total exposure, of which $72,337 is wasted render at "
        "$4.10 per node-hour."
    ),
}


def build(name: str):
    if name == "scout":
        from agent.crew.scout import build_scout
        return build_scout()
    if name == "gaffer":
        from agent.crew.gaffer import build_gaffer
        return build_gaffer()
    if name == "producer":
        from agent.crew.producer import build_producer
        return build_producer()
    if name == "first_ad":
        from agent.crew.first_ad import build_first_ad
        return build_first_ad()
    raise SystemExit(f"unknown agent {name}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="scout")
    ap.add_argument("--prompt", default=None)
    args = ap.parse_args()

    jnl = J.Journal()
    print(f"journal: {jnl.path.name}\n")
    agent = build(args.agent)
    try:
        answer = await run_agent(agent, args.prompt or PROMPTS[args.agent], jnl, actor=args.agent)
    finally:
        await aclose(agent)
        jnl.close()

    print("=" * 72); print(args.agent.upper()); print("=" * 72)
    print(answer or "(no final text)")
    print("\n" + "=" * 72); print("TOOL CALLS"); print("=" * 72)
    n = 0
    for e in jnl.events:
        if e.kind == J.TOOL_CALL:
            n += 1
            a = e.payload.get("args", {})
            q = a.get("expr") or a.get("logql") or a.get("query") or ""
            print(f"\n[{e.offset:6.1f}s] {e.payload['tool']}")
            if q: print(f"          {q}")
        elif e.kind == J.TOOL_RESULT:
            print(f"          -> {e.payload.get('latency_ms')}ms {str(e.payload.get('result',''))[:160]}")
    print(f"\n{n} tool calls")
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
