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

import argparse
import asyncio
import json
from pathlib import Path

from agent import journal as J
from agent.models import is_quota_error, mark_exhausted
from agent.runtime import aclose, run_agent

ROOT = Path(__file__).resolve().parents[1]


# --- resuming an interrupted run -------------------------------------------
# The free-tier quota is 20 requests per day PER MODEL and one crew member is
# roughly one model's daily budget, so a full crew run has no retry room: a
# 429 in the Producer used to throw away a finished Scout and Gaffer and cost
# a whole day. Each stage's output is checkpointed as it completes, so the run
# picks up at the stage that died -- on models whose own quota is untouched.
#
# This is a continuation, not a stitch of separate runs: the journal is
# reopened and appended to, so the war room replays one continuous incident.

def _checkpoint_path(run_id: str) -> Path:
    return J.JOURNAL_DIR / f"{run_id}.checkpoint.json"


def _load_checkpoint(run_id: str) -> dict[str, str]:
    path = _checkpoint_path(run_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("stages", {})
    except Exception:  # noqa: BLE001 - a corrupt checkpoint just means no resume
        return {}


def _save_stage(run_id: str, stage: str, text: str) -> None:
    path = _checkpoint_path(run_id)
    stages = _load_checkpoint(run_id)
    stages[stage] = text
    path.write_text(
        json.dumps({"run_id": run_id, "stages": stages}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


CAPTIONS = {
    "scout": "SCOUT — sweeping 1,200 shots for delivery risk",
    "gaffer": "GAFFER — correlating metrics with logs to find the cause",
    "vision": "TECH CHECK — Gemini inspects the rendered frame itself",
    "producer": "PRODUCER — pricing the delay against the delivery date",
    "first_ad": "FIRST AD — writing the incident back into Grafana",
}


async def _step(
    name: str, build, prompt: str, jnl: J.Journal, done: dict[str, str] | None = None
) -> str:
    # Already completed on an earlier attempt: its events are in the journal
    # and its answer is in the checkpoint, so replay neither and spend nothing.
    if done and name in done:
        return done[name]
    jnl.record(J.CAPTION, name, text=CAPTIONS[name])

    # A local tally can only estimate what the service thinks is left, and
    # guessing wrong costs a whole run: this stage once died on a model the
    # ledger believed was fresh. So take the 429 as the authority -- record
    # that model as spent and build the stage again, which `model_for` then
    # resolves to the next model in the pool. Only stages that produced
    # nothing are retried, so nothing is paid for twice.
    last_exc: Exception | None = None
    for attempt in range(2):
        agent = build()
        model = getattr(getattr(agent, "model", None), "model", None)
        try:
            text = await run_agent(agent, prompt, jnl, actor=name)
            _save_stage(jnl.run_id, name, text)
            return text
        except Exception as exc:  # noqa: BLE001 - re-raised below if not quota
            last_exc = exc
            if attempt == 0 and model and is_quota_error(exc):
                mark_exhausted(model)
                jnl.record(
                    J.AGENT_THOUGHT,
                    name,
                    text=(
                        f"{model} is out of free quota for today. Switching "
                        f"model and starting this stage again."
                    ),
                )
                continue
            raise
        finally:
            await aclose(agent)
    raise last_exc  # unreachable; the loop either returns or raises


def _vision_step(
    jnl: J.Journal,
    shot_id: str,
    frame_no: int,
    defect: str | None,
    done: dict[str, str] | None = None,
) -> str:
    """Inspect a rendered frame. Deterministic: no model chooses to do this.

    The frame is rendered and then examined. The tech check is not told which
    defect was introduced, or whether there is one at all.
    """
    if done and "vision" in done:
        return done["vision"]

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
        summary = (
            f"TECH CHECK FAILED on {shot_id} frame {frame_no:04d}: "
            f"{verdict.verdict} ({verdict.confidence:.0%}). {verdict.evidence} "
            f"The render exited successfully and its duration was normal, so no "
            f"metric would have caught this."
        )
    else:
        summary = (
            f"TECH CHECK PASSED on {shot_id} frame {frame_no:04d}: clean and "
            f"deliverable. {verdict.evidence}"
        )
    _save_stage(jnl.run_id, "vision", summary)
    return summary


async def investigate(
    shot_id: str = "RC_0410",
    frame_no: int = 112,
    defect: str | None = "fireflies",
    jnl: J.Journal | None = None,
    resume: str | None = None,
) -> J.Journal:
    """Run the full crew end to end and return the journal of the run.

    Pass ``resume`` a previous run id to continue an interrupted run: the
    stages it already finished are read from its checkpoint and their events
    are left where they are, so only the stages that never ran cost anything.
    """
    from agent.crew.first_ad import build_first_ad
    from agent.crew.gaffer import build_gaffer
    from agent.crew.producer import build_producer
    from agent.crew.scout import build_scout

    done = _load_checkpoint(resume) if resume else {}
    jnl = jnl or J.Journal(run_id=resume, resume=bool(resume))
    if not done:
        jnl.record(
            J.RUN_START,
            "system",
            film="THE LAST TRANSMISSION",
            shots=1200,
            delivery="2026-09-30",
        )
    else:
        print(f"resuming {jnl.run_id}: already done -> {', '.join(done)}")

    scout = await _step(
        "scout",
        build_scout,
        "Sweep the render farm now. Which shots are at risk of missing the "
        "delivery date, and what is the farm doing that puts them there?",
        jnl,
        done,
    )

    gaffer = await _step(
        "gaffer",
        build_gaffer,
        "Scout reports:\n\n"
        f"{scout}\n\n"
        "Establish the cause from the telemetry and the logs. Name the resource "
        "or node responsible, quote your evidence, and say what you ruled out.",
        jnl,
        done,
    )

    vision = _vision_step(jnl, shot_id, frame_no, defect, done)

    producer = await _step(
        "producer",
        build_producer,
        "The Gaffer reports:\n\n"
        f"{gaffer}\n\n"
        f"{vision}\n\n"
        "Price what this does to the 30 September delivery date.",
        jnl,
        done,
    )

    await _step(
        "first_ad",
        build_first_ad,
        "Record this investigation in Grafana and write the production note.\n\n"
        f"CAUSE AND EVIDENCE:\n{gaffer}\n\n"
        f"TECH CHECK:\n{vision}\n\n"
        f"DELIVERY AND COST:\n{producer}",
        jnl,
        done,
    )

    jnl.record(J.RUN_END, "system")
    jnl.close()
    return jnl


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(prog="agent.orchestrator")
    parser.add_argument(
        "--resume",
        metavar="RUN_ID",
        help="continue an interrupted run, skipping the stages it finished",
    )
    args = parser.parse_args(argv)

    load_dotenv(ROOT / ".env")
    jnl = J.Journal(run_id=args.resume, resume=bool(args.resume))
    try:
        asyncio.run(investigate(jnl=jnl, resume=args.resume))
    except Exception as exc:  # noqa: BLE001 - the point is to report, then exit
        done = _load_checkpoint(jnl.run_id)
        print(f"\nrun stopped: {type(exc).__name__}: {exc}")
        if done:
            print(f"stages completed and checkpointed: {', '.join(done)}")
            print(
                "The finished stages are kept. Continue on fresh quota with:\n"
                f"    python -m agent.orchestrator --resume {jnl.run_id}"
            )
        else:
            print("nothing completed; nothing to resume.")
        print(f"partial journal: {jnl.path}")
        return 1
    print(f"\njournal written: {jnl.path}")
    print(J.summarise(jnl.path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
