"""Read the farm's current position straight out of Grafana, in Python.

Producer originally took its inputs from the model: the agent read metrics,
then passed the numbers into the costing tool. That failed in a specific and
instructive way. The costing itself was deterministic, but the *inputs* were
not -- in one run the agent called the tool twice with different readings and
produced $172,515 and then $283,720 for the same farm, in the same minute.

Making the maths deterministic is not enough if a model chooses what goes into
it. So the tool now takes no arguments and reads its own telemetry through the
Grafana datasource proxy. The agent decides *when* to price the risk; it has no
say in what the numbers are.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

import httpx

from agent.economics import FarmReading
from agent.mcp import PROMETHEUS_UID

#: The film. These are properties of the production, not of the farm.
FRAMES_TOTAL = 161_200
SHOTS_TOTAL = 1_200
NODES_TOTAL = 200


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=os.environ["GRAFANA_URL"].rstrip("/"),
        headers={
            "Authorization": f"Bearer {os.environ['GRAFANA_SERVICE_ACCOUNT_TOKEN']}"
        },
        timeout=30.0,
    )


class StaleTelemetry(RuntimeError):
    """No recent data for a metric the costing genuinely depends on.

    Raised rather than defaulted. An earlier version returned 0.0 for a missing
    metric, and the "deterministic" costing then reported a 6,691-day slip and
    $132m of exposure from an idle farm. A number built on absent data is worse
    than an error, because it looks like an answer.
    """


def _instant(c: httpx.Client, expr: str, default: float | None = None) -> float:
    """Run one instant PromQL query and return a single scalar.

    Raises StaleTelemetry when there is no data and no default was given.
    """
    now = int(time.time() * 1000)
    body = {
        "queries": [
            {
                "refId": "A",
                "datasource": {"type": "prometheus", "uid": PROMETHEUS_UID},
                "expr": expr,
                "instant": True,
            }
        ],
        "from": str(now - 6 * 60 * 60 * 1000),
        "to": str(now),
    }
    try:
        r = c.post("/api/ds/query", json=body)
        r.raise_for_status()
        frames = r.json()["results"]["A"].get("frames", [])
        for f in frames:
            values = f.get("data", {}).get("values", [])
            # Prometheus instant frames come back as [[time], [value]].
            if len(values) >= 2 and values[1]:
                return float(values[1][0])
    except Exception as exc:  # noqa: BLE001
        if default is None:
            raise StaleTelemetry(f"query failed: {expr}") from exc
        return default
    if default is None:
        raise StaleTelemetry(
            f"no data for {expr!r} in the last hour. Is the farm simulator "
            f"running? Start it with: python -m sim.main"
        )
    return default


def read_farm(delivery_date: datetime | None = None) -> FarmReading:
    """Snapshot the farm's delivery position from live telemetry."""
    delivery = delivery_date or datetime.fromisoformat(
        os.environ.get("SHOT_CLOCK_DELIVERY_DATE", "2026-09-30") + "T23:59:00"
    )
    with _client() as c:
        frames_rendered = int(_instant(c, "sum(last_over_time(shot_frames_completed_total[1h]))"))
        now_rate = _instant(c, "last_over_time(farm_frames_per_hour[1h])")
        # The healthy baseline is the best the farm has managed recently. Using
        # a max rather than an average avoids the baseline sagging toward the
        # fault as the fault persists.
        healthy_rate = _instant(c, "max_over_time(farm_frames_per_hour[6h])", now_rate)
        # NB: the farm also publishes a `shots_at_risk` gauge, computed from
        # its own per-shot queue projection. It is deliberately NOT used here.
        # The two projections disagreed -- the gauge reported 908 shots late in
        # the same breath as this model reported the farm landing 111h early --
        # and a costing that contradicts its own headline is worthless. At-risk
        # is derived below from the same throughput arithmetic as the slip, so
        # the two cannot diverge.
        degraded = int(
            _instant(c, 'count(last_over_time(node_memory_bytes{health!="healthy"}[1h]))', 0.0)
        )

    now = datetime.now()
    # If the clock has already passed the nominal delivery date, price against
    # the next one rather than reporting a negative window.
    while delivery <= now:
        delivery += timedelta(days=1)

    # How many shots cannot finish before the date at the current rate.
    frames_remaining = max(FRAMES_TOTAL - frames_rendered, 0)
    hours_left = max((delivery - now).total_seconds() / 3600.0, 0.0)
    deliverable = max(now_rate, 1.0) * hours_left
    shortfall = max(frames_remaining - deliverable, 0.0)
    frames_per_shot = FRAMES_TOTAL / SHOTS_TOTAL
    shots_at_risk = min(int(round(shortfall / frames_per_shot)), SHOTS_TOTAL)

    return FarmReading(
        frames_total=FRAMES_TOTAL,
        frames_rendered=frames_rendered,
        frames_per_hour_now=max(now_rate, 1.0),
        frames_per_hour_healthy=max(healthy_rate, now_rate, 1.0),
        nodes_total=NODES_TOTAL,
        nodes_affected=degraded if degraded else NODES_TOTAL,
        shots_at_risk=shots_at_risk,
        shots_total=SHOTS_TOTAL,
        delivery_date=delivery,
        now=now,
    )
