"""Run the render farm and stream its telemetry to Grafana Cloud.

    python -m sim.main --fault oom
    python -m sim.main --dry-run --duration 30      # no credentials needed

The farm advances in simulated minutes on a real-time tick, so an hour of
production passes in a couple of minutes of wall clock and a fault develops
fast enough to watch on a dashboard.
"""
from __future__ import annotations

import argparse
import logging
import random
import signal
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from sim import faults
from sim.farm import Farm

ROOT = Path(__file__).resolve().parents[1]

#: Simulated seconds advanced per real tick. One tick is ten production minutes.
SIM_SECONDS_PER_TICK = 600.0

_running = True


def _stop(signum, frame) -> None:  # noqa: ANN001 - signal handler signature
    global _running
    _running = False
    print("\nstopping, flushing telemetry...", file=sys.stderr)


# --- renderer chatter -------------------------------------------------------
# Arnold and Karma write to stderr in a recognisable register. These are the
# healthy lines; the faults contribute their own, more alarming ones.
_ROUTINE = (
    "[{r}] INFO  | {shot} frame {frame:04d} complete in {secs:.1f}s",
    "[{r}] INFO  | {shot} loading scene, {mb} MB of geometry",
    "[{r}] INFO  | {shot} texture cache {hit:.0%} hit rate on {node}",
    "[{r}] DEBUG | {shot} denoise pass complete, {secs:.1f}s",
)
_GRUMBLES = (
    "[{r}] WARNING | {shot} subdivision limit reached on /obj/hero_geo, clamping",
    "[{r}] WARNING | {shot} displacement bound exceeded, growing BVH",
    "[{r}] WARNING | {shot} texture <tex>/{shot}_diffuse.tx not found, using fallback",
)


def _emit_routine(log: logging.Logger, farm: Farm, rng: random.Random) -> None:
    """Emit a few lines of ordinary renderer output for the in-flight shots."""
    shots = farm.shot_states()
    if not shots:
        return
    summary = farm.summary()
    for shot in rng.sample(shots, min(3, len(shots))):
        grumbling = rng.random() < 0.18
        template = rng.choice(_GRUMBLES if grumbling else _ROUTINE)
        message = template.format(
            r=shot.renderer,
            shot=shot.shot_id,
            frame=max(shot.frames_done, 1),
            secs=shot.mean_frame_seconds or 90.0,
            mb=rng.randrange(400, 9000),
            hit=summary.texture_cache_hit_ratio,
            node=shot.node or "unassigned",
        )
        # The level has to match the register of the line, or a LogQL query for
        # warnings misses half the renderer's complaints.
        log.log(
            logging.WARNING if grumbling else logging.INFO,
            message,
            extra={
                "shot_id": shot.shot_id,
                "sequence": shot.sequence,
                "renderer": shot.renderer,
                "artist": shot.artist,
                "node": shot.node or "unassigned",
            },
        )


def _emit_fault_events(log: logging.Logger, farm: Farm) -> None:
    """Drain whatever the active fault wants to say this tick."""
    fault = faults.active(farm)
    if fault is None:
        return
    for event in fault.drain_events():
        log.log(event.level, event.message, extra=dict(event.attributes))


def _print_status(farm: Farm) -> None:
    s = farm.summary()
    print(
        f"{s.sim_time:%d %b %H:%M}  "
        f"in-flight {s.shots_in_flight:2d}  done {s.shots_complete:4d}  "
        f"at-risk {s.shots_at_risk:4d}  queue {s.queue_depth:3d}  "
        f"lic {s.licences_available:3d}/{s.licences_total}  "
        f"cache {s.texture_cache_hit_ratio:.2f}  "
        f"nodes {s.nodes_busy}/{s.nodes_total}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sim.main", description="Render farm simulator for Shot Clock"
    )
    parser.add_argument(
        "--fault",
        choices=(*faults.FAULTS, "none"),
        default="none",
        help="inject a fault once the farm has settled",
    )
    parser.add_argument(
        "--fault-after",
        type=int,
        default=6,
        help="ticks of healthy running before the fault is injected (default 6)",
    )
    parser.add_argument(
        "--duration", type=float, default=0.0, help="real seconds to run, 0 = forever"
    )
    parser.add_argument(
        "--tick", type=float, default=5.0, help="real seconds between ticks"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="do not export to Grafana; print locally instead. Needs no credentials.",
    )
    parser.add_argument("--seed", type=int, default=90210)
    args = parser.parse_args(argv)

    load_dotenv(ROOT / ".env")

    farm = Farm(seed=args.seed)
    rng = random.Random(args.seed ^ 0x5C10CC)

    telemetry = None
    if args.dry_run:
        logging.basicConfig(
            level=logging.INFO, format="%(levelname)-7s %(message)s", stream=sys.stdout
        )
        log = logging.getLogger("renderfarm")
        print("dry run: telemetry is printed locally, nothing is sent to Grafana\n")
    else:
        # Imported lazily so --dry-run works with no OTLP settings present.
        from sim.telemetry import FarmTelemetry, MissingCredentials, log

        try:
            telemetry = FarmTelemetry(farm)
            telemetry.start()
        except MissingCredentials as exc:
            print(f"\ncannot start: {exc}", file=sys.stderr)
            print(
                "Fill in .env (copy it from .env.example), or pass --dry-run.\n",
                file=sys.stderr,
            )
            return 2
        print(f"exporting to {farm.summary().sim_time:%d %b %Y} farm -> Grafana Cloud\n")

    # Traces only exist when we are actually exporting; --dry-run has no
    # tracer provider behind it.
    tracer = None
    if not args.dry_run:
        from sim.tracing import FrameTracer

        tracer = FrameTracer(seed=args.seed)

    signal.signal(signal.SIGINT, _stop)

    started = time.monotonic()
    ticks = 0
    try:
        while _running:
            farm.tick(SIM_SECONDS_PER_TICK)
            ticks += 1

            if args.fault != "none" and ticks == args.fault_after:
                fault = faults.inject(farm, args.fault)
                print(f"\n>>> injected {args.fault}: {fault.describe()}\n")

            _emit_routine(log, farm, rng)
            _emit_fault_events(log, farm)
            if tracer is not None:
                tracer.observe(farm)
            _print_status(farm)

            if args.duration and time.monotonic() - started >= args.duration:
                break
            time.sleep(args.tick)
    finally:
        if telemetry is not None:
            telemetry.shutdown()

    print(f"\nran {ticks} ticks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
