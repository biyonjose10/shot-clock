"""Turn telemetry into hours and dollars, in Python, never in the model.

Every figure the war room shows is computed here from numbers read out of
Grafana. The agents narrate these values; they never derive them.

That is a deliberate constraint, and it is worth stating plainly because it is
easy to get wrong: a language model asked to "estimate the cost of the delay"
will produce a confident, plausible, *different* number every run. On a demo
video that is fatal -- a judge who multiplies 12.3 node-hours by $4.10 and does
not get $18,400 stops believing the entire project. So the maths lives here,
the figures reconcile with each other by construction, and
``CostEstimate.check()`` asserts that they do.

The rates are documented assumptions, not claims of truth. A real facility
would substitute its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

# --- documented assumptions ------------------------------------------------
#: Cost of one render node for one hour. Mid-range for on-prem farm compute
#: once power, cooling, amortised hardware and facility overhead are included.
NODE_HOUR_RATE = 4.10

#: A shot that misses delivery does not just cost compute. It burns supervisor
#: and artist attention: re-briefing, re-submitting, a dailies round trip.
SHOT_SLIP_HANDLING_HOURS = 1.5

#: Blended rate for that handling time.
ARTIST_HOUR_RATE = 62.00

#: What a studio pays to compress the remaining schedule when delivery is
#: threatened: burst capacity at premium rates, per node-hour above baseline.
SURGE_PREMIUM_MULTIPLIER = 2.2


@dataclass
class FarmReading:
    """The telemetry the estimate is built from. All read from Grafana."""

    frames_total: int
    frames_rendered: int
    frames_per_hour_now: float
    frames_per_hour_healthy: float
    nodes_total: int
    nodes_affected: int
    shots_at_risk: int
    shots_total: int
    delivery_date: datetime
    now: datetime

    @property
    def frames_remaining(self) -> int:
        return max(self.frames_total - self.frames_rendered, 0)

    @property
    def hours_to_delivery(self) -> float:
        return max((self.delivery_date - self.now).total_seconds() / 3600.0, 0.0)


@dataclass
class CostEstimate:
    """Hours and dollars, with every intermediate value kept for display."""

    reading: FarmReading

    hours_needed_now: float = 0.0
    hours_needed_healthy: float = 0.0
    hours_to_delivery: float = 0.0
    slip_hours: float = 0.0
    projected_finish: datetime | None = None

    throughput_lost_pct: float = 0.0
    wasted_node_hours: float = 0.0
    wasted_compute_cost: float = 0.0
    slip_handling_cost: float = 0.0
    surge_cost_to_recover: float = 0.0
    total_exposure: float = 0.0

    notes: list[str] = field(default_factory=list)

    def check(self) -> None:
        """Assert the figures reconcile, so the UI can never show nonsense."""
        assert abs(self.wasted_compute_cost - self.wasted_node_hours * NODE_HOUR_RATE) < 0.01, (
            "wasted compute cost must equal wasted node-hours times the rate"
        )
        expected_total = (
            self.wasted_compute_cost + self.slip_handling_cost + self.surge_cost_to_recover
        )
        assert abs(self.total_exposure - expected_total) < 0.01, (
            "total exposure must be the sum of its components"
        )
        assert self.slip_hours >= 0.0


def estimate(reading: FarmReading) -> CostEstimate:
    """Compute the delivery and cost position from one telemetry reading."""
    est = CostEstimate(reading=reading)

    now_rate = max(reading.frames_per_hour_now, 1e-6)
    healthy_rate = max(reading.frames_per_hour_healthy, now_rate)

    est.hours_needed_now = reading.frames_remaining / now_rate
    est.hours_needed_healthy = reading.frames_remaining / healthy_rate
    est.hours_to_delivery = reading.hours_to_delivery

    # The slip is how far past the date the current rate lands us. If the farm
    # is still inside the date, the slip is zero -- there is no exposure to
    # inflate, and saying otherwise would be the same sin as a made-up number.
    est.slip_hours = max(est.hours_needed_now - est.hours_to_delivery, 0.0)
    est.projected_finish = reading.now + timedelta(hours=est.hours_needed_now)

    est.throughput_lost_pct = max(0.0, (1.0 - now_rate / healthy_rate) * 100.0)

    # Wasted compute: the affected nodes are running, drawing power and costing
    # money, but producing less than they should. The waste is the difference
    # between what those node-hours should have yielded and what they did.
    degraded_hours = est.hours_needed_now - est.hours_needed_healthy
    est.wasted_node_hours = round(max(degraded_hours, 0.0) * max(reading.nodes_affected, 0), 1)
    est.wasted_compute_cost = round(est.wasted_node_hours * NODE_HOUR_RATE, 2)

    est.slip_handling_cost = round(
        reading.shots_at_risk * SHOT_SLIP_HANDLING_HOURS * ARTIST_HOUR_RATE, 2
    )

    # To land on the date, the missing frames have to be bought back with burst
    # capacity, billed at a premium.
    frames_at_risk = max(est.slip_hours, 0.0) * now_rate
    recover_node_hours = (frames_at_risk / max(healthy_rate, 1e-6)) * reading.nodes_total
    est.surge_cost_to_recover = round(
        recover_node_hours * NODE_HOUR_RATE * SURGE_PREMIUM_MULTIPLIER, 2
    )

    est.total_exposure = round(
        est.wasted_compute_cost + est.slip_handling_cost + est.surge_cost_to_recover, 2
    )

    est.notes = [
        f"node time billed at ${NODE_HOUR_RATE:.2f}/node-hour",
        f"{reading.shots_at_risk} shots at risk x {SHOT_SLIP_HANDLING_HOURS}h "
        f"handling x ${ARTIST_HOUR_RATE:.2f}/h",
        f"recovery priced at {SURGE_PREMIUM_MULTIPLIER}x standard rate",
    ]

    est.check()
    return est


def headline(est: CostEstimate) -> str:
    """One sentence a VFX supervisor can act on."""
    r = est.reading
    if est.slip_hours <= 0:
        return (
            f"{r.shots_at_risk} shots flagged, but the farm still lands "
            f"{est.hours_to_delivery - est.hours_needed_now:.0f}h inside the date."
        )
    days = est.slip_hours / 24.0
    return (
        f"{r.shots_at_risk} of {r.shots_total} shots are projected past "
        f"{r.delivery_date:%d %b}. At the current rate delivery slips "
        f"{est.slip_hours:.0f}h ({days:.1f} days), exposing "
        f"${est.total_exposure:,.0f}."
    )


def as_tiles(est: CostEstimate) -> list[dict[str, str]]:
    """The costing tiles for the war room, pre-formatted and reconciling."""
    r = est.reading
    return [
        {"label": "Shots projected late", "value": f"{r.shots_at_risk} of {r.shots_total}"},
        {"label": "Delivery slip", "value": f"{est.slip_hours:.0f}h past {r.delivery_date:%d %b}"},
        {
            "label": "Throughput lost",
            "value": f"{est.throughput_lost_pct:.0f}% "
            f"({r.frames_per_hour_healthy:,.0f} to {r.frames_per_hour_now:,.0f} frames/h)",
        },
        {
            "label": "Wasted render",
            "value": f"{est.wasted_node_hours:,.1f} node-hours "
            f"= ${est.wasted_compute_cost:,.0f}",
        },
        {"label": "Total exposure", "value": f"${est.total_exposure:,.0f}"},
    ]
