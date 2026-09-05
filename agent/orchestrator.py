"""Run the whole crew on one incident, and journal it.

The four crew members are autonomous ADK agents: each chooses its own Grafana
tools and reaches its own conclusions. What is *not* left to a model is the
order they run in. A model-routed orchestrator would add a round trip per hop,
burn quota deciding something we already know, and — worst of all — pick a
different order on the take we are filming.

So the sequence is fixed and the autonomy sits inside each agent, where it
earns something. Between Gaffer and Producer there is a deterministic step with
no model routing at all: the vision tech-check, which looks at the actual
rendered frame. That is the beat telemetry structurally cannot reach.

Everything lands in a journal, which is what the war room replays.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from agent import journal as J
from agent.runtime import aclose, run_agent

ROOT = Path(__file__).resolve().parents[1]


CAPTIONS = {
    "scout": "SCOUT — sweeping 1,200 shots for delivery risk",
    "gaffer": "GAFFER — correlating metrics with logs to find the cause",
    "vision": "TECH CHECK — Gemini inspects the rendered frame itself",
    "producer": "PRODUCER — pricing the delay against the delivery date",
    "first_ad": "FIRST AD — writing the incident back into Grafana",
}


async def _step(name: str, build, prompt: str, jnl: J.Journal) -> str:
    jnl.record(J.CAPTION, name, text=CAPTIONS[name])
    agent = build()
    try:
        return await run_agent(agent, prompt, jnl, actor=name)
    finally:
        await aclose(agent)


def _vision_step(jnl: J.Journal, shot_id: str, frame_no: int, defect: str | None) -> str:
    """Inspect a rendered frame. Deterministic: no model chooses to do this.

    The frame is rendered and then examined. The tech check is not told which
    defect was introduced, or whether there is one at all.
    """
    from agent.vision import tech_check
    from sim.frames import render_frame

    jnl.record(J.CAPTION, "vision", text=CAPTIONS["vision"])
    path = render_frame(shot_id, frame_no, defect)
    jnl.record(
        J.AGENT_THOUGHT,
        "vision",
        text=(
            f"{shot_id} frame {frame_no:04d} reported success and normal duration. "
            f"Pulling the plate and looking at it."
        ),
    )
    verdict = tech_check(path)
    jnl.record(
        J.VISION_VERDICT,
        "vision",
        shot_id=shot_id,
        frame=frame_no,
        verdict=verdict.verdict,
        confidence=verdict.confidence,
        evidence=verdict.evidence,
        region=verdict.region,
        deliverable=verdict.deliverable,
        # Served by the web app, so the war room can show the plate.
        image="/static/frames/" + path.name,
    )
    if verdict.is_defect:
        return (
            f"TECH CHECK FAILED on {shot_id} frame {frame_no:04d}: "
            f"{verdict.verdict} ({verdict.confidence:.0%}). {verdict.evidence} "
            f"The render exited successfully and its duration was normal, so no "
            f"metric would have caught this."
        )
    return (
        f"TECH CHECK PASSED on {shot_id} frame {frame_no:04d}: clean and "
        f"deliverable. {verdict.evidence}"
    )


async def investigate(
    shot_id: str = "RC_0410",
    frame_no: int = 112,
    defect: str | None = "fireflies",
    jnl: J.Journal | None = None,
) -> J.Journal:
    """Run the full crew end to end and return the journal of the run."""
    from agent.crew.first_ad import build_first_ad
    from agent.crew.gaffer import build_gaffer
    from agent.crew.producer import build_producer
    from agent.crew.scout import build_scout

    jnl = jnl or J.Journal()
    jnl.record(
        J.RUN_START,
        "system",
        film="THE LAST TRANSMISSION",
        shots=1200,
        delivery="2026-09-30",
    )

    scout = await _step(
        "scout",
        build_scout,
        "Sweep the render farm now. Which shots are at risk of missing the "
        "delivery date, and what is the farm doing that puts them there?",
        jnl,
    )

    gaffer = await _step(
        "gaffer",
        build_gaffer,
        "Scout reports:\n\n"
        f"{scout}\n\n"
        "Establish the cause from the telemetry and the logs. Name the resource "
        "or node responsible, quote your evidence, and say what you ruled out.",
        jnl,
    )

    vision = _vision_step(jnl, shot_id, frame_no, defect)

    producer = await _step(
        "producer",
        build_producer,
        "The Gaffer reports:\n\n"
        f"{gaffer}\n\n"
        f"{vision}\n\n"
        "Price what this does to the 30 September delivery date.",
        jnl,
    )

    await _step(
        "first_ad",
        build_first_ad,
        "Record this investigation in Grafana and write the production note.\n\n"
        f"CAUSE AND EVIDENCE:\n{gaffer}\n\n"
        f"TECH CHECK:\n{vision}\n\n"
        f"DELIVERY AND COST:\n{producer}",
        jnl,
    )

    jnl.record(J.RUN_END, "system")
    jnl.close()
    return jnl


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    jnl = asyncio.run(investigate())
    print(f"\njournal written: {jnl.path}")
    print(J.summarise(jnl.path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
