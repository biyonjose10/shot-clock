"""Producer — converts a fault into a delivery date and a number.

A supervisor does not act on "texture cache hit ratio is 0.37". They act on
"we land 31 hours past the date and it costs $88,000". Producer is the
translation layer.

Crucially it does NOT do the arithmetic. It reads the telemetry out of Grafana
and passes those readings to `price_delivery_risk`, a deterministic Python tool
that computes and self-checks the figures. The model narrates the result; it
never derives it. Ask a language model to estimate a cost and it produces a
confident, plausible, different number every run -- fatal on a single-take demo
where a judge can multiply two of the numbers together.
"""
from __future__ import annotations

from google.adk.agents import LlmAgent

from agent.economics import FarmReading, as_tiles, estimate, headline
from agent.mcp import DATASOURCE_BRIEFING, PRODUCER_TOOLS, toolset
from agent.models import crew_llm


def price_delivery_risk() -> dict:
    """Price the current delivery risk from live farm telemetry.

    Takes no arguments on purpose. It reads the farm's position out of Grafana
    itself and computes every figure deterministically, so the same farm state
    always produces the same numbers. Call it once.

    Returns:
        The headline sentence, the display tiles, and every intermediate
        figure, all guaranteed to reconcile with each other.
    """
    from agent.readings import read_farm

    reading = read_farm()
    est = estimate(reading)
    return {
        "headline": headline(est),
        "tiles": as_tiles(est),
        "read_from_grafana": {
            "frames_rendered": reading.frames_rendered,
            "frames_per_hour_now": round(reading.frames_per_hour_now, 1),
            "frames_per_hour_healthy": round(reading.frames_per_hour_healthy, 1),
            "shots_at_risk": reading.shots_at_risk,
            "hours_to_delivery": round(reading.hours_to_delivery, 1),
        },
        "slip_hours": round(est.slip_hours, 1),
        "throughput_lost_pct": round(est.throughput_lost_pct, 1),
        "wasted_node_hours": est.wasted_node_hours,
        "wasted_compute_cost": est.wasted_compute_cost,
        "total_exposure": est.total_exposure,
        "assumptions": est.notes,
    }


INSTRUCTION = f"""
You are the Producer on a feature film in delivery. The Gaffer has found what
is wrong with the render farm. You answer the only question the studio cares
about: does this cost us the delivery date, and what is that worth in money.

{DATASOURCE_BRIEFING}

METHOD

1. Call `price_delivery_risk` ONCE. It takes no arguments: it reads the farm's
   position out of Grafana itself and returns every figure, already reconciled.
2. Optionally read `queue_depth` or `texture_cache_hit_ratio` from Prometheus
   for one line of colour. Do not go hunting through metrics; the costing tool
   has already read what matters.
3. Report what the tool returned.

RULES

- NEVER compute or estimate a cost or an hour figure yourself, and never call
  `price_delivery_risk` more than once. Every number you state must be one the
  tool returned, copied exactly.
- If the tool says the slip is zero, report that the farm is inside the date.
  Do not manufacture a crisis; an agent that always finds a disaster is not
  worth having.
- Quote the assumptions the tool returns. A number without its rate is not
  auditable, and a supervisor will ask.

OUTPUT

  POSITION: the headline sentence from the tool.
  NUMBERS: the tiles, one per line.
  ASSUMPTIONS: the rates used.
  RECOMMENDATION: one sentence -- what should the studio actually do.

Under 200 words.
""".strip()


def build_producer() -> LlmAgent:
    return LlmAgent(
        name="producer",
        model=crew_llm("producer"),
        description=(
            "Converts a render farm fault into delivery-date slip and dollar "
            "exposure, using a deterministic costing tool."
        ),
        instruction=INSTRUCTION,
        tools=[toolset(PRODUCER_TOOLS), price_delivery_risk],
    )
