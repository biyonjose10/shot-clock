"""ADK entry point: `root_agent`, for `adk run` / `adk web`.

ADK's convention is a module-level `root_agent` defined **synchronously**, so
the toolsets exist at import time rather than being built inside a coroutine.
A judge who clones this repo and runs `adk web` gets the crew without needing
to know about `agent.orchestrator`.

This is a `SequentialAgent` for the same reason `orchestrator.py` uses a fixed
order: a model-routed root would spend a round trip deciding something already
known, and could pick a different order on the take being filmed. Each member
is still fully autonomous once it starts -- it chooses its own Grafana queries
and reaches its own conclusions.

The one thing this entry point cannot do is the vision tech check, which is a
deterministic step between Gaffer and Producer rather than an agent. For the
full incident including that step, use:

    python -m agent.orchestrator
"""
from __future__ import annotations

from google.adk.agents import SequentialAgent

from agent.crew.first_ad import build_first_ad
from agent.crew.gaffer import build_gaffer
from agent.crew.producer import build_producer
from agent.crew.scout import build_scout

#: The crew, in the order an incident actually gets handled: notice it, prove
#: it, price it, record it.
root_agent = SequentialAgent(
    name="shot_clock_crew",
    description=(
        "An SRE crew for a VFX render farm. Watches Grafana telemetry, finds "
        "which shots will miss the delivery date, proves the cause, prices the "
        "delay, and records the investigation back into Grafana."
    ),
    sub_agents=[
        build_scout(),
        build_gaffer(),
        build_producer(),
        build_first_ad(),
    ],
)
