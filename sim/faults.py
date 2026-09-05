"""Fault injection: four render-farm failures with four different fingerprints.

The point of the demo is not that something breaks — something is always
breaking on a farm — it is that a diagnostic agent can tell *which* thing broke
from the telemetry alone. So each fault here is built to be distinguishable:

    oom                 One node's resident memory climbs to its capacity and
                        the render is killed. ``node_memory_bytes`` spikes on
                        exactly ONE node, the logs carry allocation failures,
                        and the rest of the farm is untouched.

    licence-starvation  The floating licence pool collapses. ``queue_depth``
                        climbs, ``licence_pool_available`` sits at zero, shots
                        stall mid-render — and frame durations DO NOT move,
                        because a blocked render is not a slow render.

    texture-cache-miss  ``texture_cache_hit_ratio`` falls from ~0.93 to ~0.35
                        and every frame on the floor gets several times slower.
                        Farm-wide, not one node; memory and licences normal.

    corrupt-frame       Nothing moves at all. The job status stays healthy, the
                        frame duration stays normal, no error is logged — and
                        the plate written to disk is visually wrong. This is the
                        fault metrics physically cannot see, and the reason the
                        pipeline needs a vision check on the dailies.

Faults attach to a :class:`sim.farm.Farm` through its ``tick_hooks`` seam, so
the farm itself never learns what a fault is. Injection takes effect on the
next tick, and one farm carries at most one fault at a time.

Determinism: every fault seeds its own RNG from the farm's seed, so the same
seed plus the same fault produces the same node, the same defect and the same
log lines on every run.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from pathlib import Path

from sim import frames
from sim.farm import Farm, FailureKind, Node

#: The injectable fault names, in the order the demo walks through them.
FAULTS: tuple[str, ...] = (
    "oom",
    "licence-starvation",
    "texture-cache-miss",
    "corrupt-frame",
)

#: Per-fault salt so two faults on the same farm seed do not draw the same
#: numbers. Fixed values, not ``hash()``, which is randomised per process.
_SALT: dict[str, int] = {
    "oom": 0x0011DEAD,
    "licence-starvation": 0x00FEE501,
    "texture-cache-miss": 0x00CAC4E0,
    "corrupt-frame": 0x00BADF12,
}

# --- OOM tuning ------------------------------------------------------------
#: How much extra resident memory the leaking node accumulates per tick, as a
#: fraction of the render's honest footprint. ~5 ticks from healthy to dead.
OOM_PRESSURE_PER_TICK = 0.55
#: Warn once the node is this far into its fitted memory.
OOM_WARN_UTILISATION = 0.80

# --- licence starvation tuning ---------------------------------------------
#: Seats left in the pool after the licence server falls over. Enough for a
#: couple of renders, nowhere near enough for a farm.
LICENCE_SEATS_REMAINING = 12

# --- texture cache tuning --------------------------------------------------
#: Hit ratio the cache collapses to when the asset store link degrades.
CACHE_MISS_RATIO = 0.35

# --- corrupt frame tuning --------------------------------------------------
#: Ticks between defective plates, and how many to write before stopping. A
#: handful is enough for dailies review and keeps the repo small.
CORRUPT_EVERY_TICKS = 3
CORRUPT_PLATE_LIMIT = 6


@dataclass(frozen=True)
class LogEvent:
    """One line of renderer stderr, ready to hand to ``sim.telemetry.log``.

    ``attributes`` become structured metadata on the log record so Loki can be
    queried by shot or node, rather than only grepped.
    """

    level: int
    message: str
    attributes: dict[str, object] = field(default_factory=dict)


class Fault:
    """Base class. A fault mutates the farm on every tick and narrates itself."""

    name: str = ""

    def __init__(self, farm: Farm, **kw: object) -> None:
        self.farm = farm
        self._rng = random.Random(farm.seed ^ _SALT.get(self.name, 0))
        self._events: list[LogEvent] = []
        self.configure(**kw)

    # -- lifecycle ---------------------------------------------------------

    def configure(self, **kw: object) -> None:
        """Read keyword overrides and take a snapshot of what will be changed."""

    def apply(self, farm: Farm, dt: float) -> None:
        """Called once per tick, before the farm advances anything."""

    def revert(self, farm: Farm) -> None:
        """Put back whatever :meth:`configure` and :meth:`apply` disturbed."""

    def describe(self) -> str:
        """One line for the operator, printed when the fault is injected."""
        return self.name

    # -- narration ---------------------------------------------------------

    def emit(self, level: int, message: str, **attributes: object) -> None:
        self._events.append(LogEvent(level, message, attributes))

    def drain_events(self) -> list[LogEvent]:
        """Take the log lines accumulated since the last drain."""
        events, self._events = self._events, []
        return events


# --- oom -------------------------------------------------------------------


class OutOfMemoryFault(Fault):
    """One node leaks until the render cannot allocate, then does it again.

    A real one of these is usually a plugin that never frees its texture handles
    on a single misconfigured box. The node's memory climbs across ticks, the
    render dies with an allocation failure, the scheduler hands the node another
    shot, and the climb starts over — a sawtooth on one series and one series
    only.
    """

    name = "oom"

    def configure(self, node: str | None = None, **kw: object) -> None:
        self.node_name = node
        self._failures_seen = len(self.farm.failures)

    def _target(self, farm: Farm) -> Node:
        """The leaking node: pinned on first use so the spike never moves."""
        if self.node_name is None:
            busy = [n for n in farm._node_order if farm.nodes[n].current_shot_id]
            self.node_name = busy[0] if busy else farm._node_order[0]
        return farm.nodes[self.node_name]

    def apply(self, farm: Farm, dt: float) -> None:
        node = self._target(farm)
        shot_id = node.current_shot_id
        node.memory_pressure += OOM_PRESSURE_PER_TICK

        # The farm's own OOM check fires inside tick(); pick up its verdict from
        # the failure log rather than duplicating the threshold here.
        for event in farm.failures[self._failures_seen :]:
            if event.node == node.name and event.kind == str(FailureKind.OUT_OF_MEMORY):
                self._narrate_death(farm, event.shot_id, event.frames_done)
                node.memory_pressure = 1.0
        self._failures_seen = len(farm.failures)

        if shot_id is None or node.memory_pressure <= 1.0:
            return
        used = node.memory_used_gb
        if used >= node.memory_capacity_gb * OOM_WARN_UTILISATION:
            self._narrate_pressure(farm, shot_id, node, used)

    def _narrate_pressure(
        self, farm: Farm, shot_id: str, node: Node, used: float
    ) -> None:
        shot = farm.film.get_shot(shot_id)
        self.emit(
            logging.WARNING,
            f"[{shot.renderer}] WARNING | heap high water {used:.1f}GB of "
            f"{node.memory_capacity_gb}GB on {node.name}, flushing texture cache",
            shot_id=shot_id,
            node=node.name,
            renderer=shot.renderer,
            sequence=shot.sequence,
            artist=shot.artist,
        )

    def _narrate_death(self, farm: Farm, shot_id: str, frames_done: int) -> None:
        shot = farm.film.get_shot(shot_id)
        node = farm.nodes[self.node_name or ""]
        wanted = round(2.0 + self._rng.random() * 4.0, 1)
        for message in (
            f"[{shot.renderer}] ERROR | unable to allocate {wanted}GB for BVH, "
            f"aborting",
            f"[{shot.renderer}] ERROR | render aborted at frame {frames_done:04d} "
            f"on {node.name}, exit code 137 (killed)",
        ):
            self.emit(
                logging.ERROR,
                message,
                shot_id=shot_id,
                node=node.name,
                renderer=shot.renderer,
                sequence=shot.sequence,
                artist=shot.artist,
            )

    def revert(self, farm: Farm) -> None:
        for node in farm.nodes.values():
            node.memory_pressure = 1.0

    def describe(self) -> str:
        return "one node leaks memory until its render is OOM-killed"


# --- licence starvation ----------------------------------------------------


class LicenceStarvationFault(Fault):
    """The floating licence pool collapses; renders block, they do not slow.

    When a licence server loses its pool, running renders cannot renew their
    heartbeat and park on the re-checkout with their frame buffers intact. Work
    stops without a single error: queue depth climbs, available seats sit at
    zero, and every frame that does get rendered takes exactly as long as it
    always did. That last part is the tell that separates this from a cache
    collapse.
    """

    name = "licence-starvation"

    def configure(
        self, remaining: int = LICENCE_SEATS_REMAINING, **kw: object
    ) -> None:
        self.remaining = int(remaining)
        self.original_total = self.farm.licences_total
        self._announced = False

    def apply(self, farm: Farm, dt: float) -> None:
        farm.licences_total = self.remaining
        if not self._announced:
            self._announced = True
            self.emit(
                logging.ERROR,
                f"[licence] ERROR | flexlm://licence-01:5053 pool degraded, "
                f"{self.remaining} of {self.original_total} seats recoverable",
                node="licence-01",
                renderer="flexlm",
            )

        summary = farm.summary()
        for shot_id in sorted(farm.suspended)[:3]:
            shot = farm.film.get_shot(shot_id)
            self.emit(
                logging.WARNING,
                f"[{shot.renderer}] WARNING | licence checkout failed for "
                f"{shot.renderer}_render, {summary.licences_available} of "
                f"{summary.licences_total} seats free, {summary.queue_depth} "
                f"renders queued, retrying in 30s",
                shot_id=shot_id,
                node=farm.suspended[shot_id].node_name,
                renderer=shot.renderer,
                sequence=shot.sequence,
                artist=shot.artist,
            )

    def revert(self, farm: Farm) -> None:
        farm.licences_total = self.original_total

    def describe(self) -> str:
        return (
            f"licence pool cut to {self.remaining} seats; renders stall waiting "
            f"for a checkout"
        )


# --- texture cache miss ----------------------------------------------------


class TextureCacheMissFault(Fault):
    """The shared texture cache goes cold and every frame on the farm slows.

    Textures are read as ``.tx`` tiles from a cache in front of the asset store.
    Lose the cache — a purge, a full volume, a flapping mount — and every bucket
    refetches over the network before it can shade. The signature is farm-wide
    and uniform: frame durations rise everywhere at once while memory, licences
    and node health stay exactly where they were.
    """

    name = "texture-cache-miss"

    #: Textures the log claims to be missing. Real paths from a real-looking
    #: asset tree; the sequence directory matches the shot being rendered.
    ASSETS: tuple[str, ...] = (
        "props/crate_lid_A_diffuse",
        "env/facade_brick_02_displace",
        "chars/pilot_suit_specular",
        "env/road_wet_roughness",
        "props/antenna_array_normal",
        "env/dust_atlas_04",
    )

    def configure(self, ratio: float = CACHE_MISS_RATIO, **kw: object) -> None:
        self.ratio = float(ratio)
        self.original_target = self.farm.texture_cache_target

    def apply(self, farm: Farm, dt: float) -> None:
        farm.texture_cache_target = self.ratio

        shots = farm.shot_states()
        if not shots:
            return
        ratio = farm.texture_cache_hit_ratio

        # A couple of missing-texture warnings, then the farm-wide summary line
        # that says what the frame times are actually doing.
        for shot in self._sample(shots, 2):
            asset = self.ASSETS[self._rng.randrange(len(self.ASSETS))]
            code = shot.shot_id.split("_", 1)[0].lower()
            refetch = round(1.8 + self._rng.random() * 4.5, 1)
            self.emit(
                logging.WARNING,
                f"[{shot.renderer}] WARNING | texture "
                f"/assets/{code}/{asset}.tx not in cache, refetching from "
                f"/mnt/assets ({refetch}s)",
                shot_id=shot.shot_id,
                node=shot.node,
                renderer=shot.renderer,
                sequence=shot.sequence,
                artist=shot.artist,
            )

        slowest = max(shots, key=lambda s: s.mean_frame_seconds)
        self.emit(
            logging.WARNING,
            f"[{slowest.renderer}] WARNING | texture cache hit ratio {ratio:.2f}, "
            f"tile server thrashing, frame times inflated "
            f"{farm.texture_cache_penalty:.1f}x",
            shot_id=slowest.shot_id,
            node=slowest.node,
            renderer=slowest.renderer,
            sequence=slowest.sequence,
            artist=slowest.artist,
        )

    def _sample(self, shots: list, count: int) -> list:
        """Deterministic pick of ``count`` shots from an ordered list."""
        return [shots[self._rng.randrange(len(shots))] for _ in range(count)]

    def revert(self, farm: Farm) -> None:
        farm.texture_cache_target = self.original_target

    def describe(self) -> str:
        return (
            f"texture cache hit ratio driven to {self.ratio:.2f}; frame times "
            f"rise farm-wide"
        )


# --- corrupt frame ---------------------------------------------------------


class CorruptFrameFault(Fault):
    """The render succeeds and writes a broken plate. Metrics stay perfect.

    Every number the farm reports is healthy: the job status is rendering, the
    frame duration is nominal, memory is nominal, no error is logged. The only
    evidence is in the picture — fireflies, a magenta hole where a texture
    should be, a plate that came back black. This is deliberately invisible to
    every gauge in ``sim.telemetry``: it exists to be caught by a vision check
    on the dailies, and by nothing else.

    The farm state is never touched. Not once.
    """

    name = "corrupt-frame"

    def configure(
        self,
        shot_id: str | None = None,
        defect: str | None = None,
        every: int = CORRUPT_EVERY_TICKS,
        limit: int = CORRUPT_PLATE_LIMIT,
        out_dir: Path | str | None = None,
        **kw: object,
    ) -> None:
        self.shot_id = shot_id
        self.defect = defect or frames.DEFECTS[self._rng.randrange(len(frames.DEFECTS))]
        if self.defect not in frames.DEFECTS:
            raise ValueError(
                f"unknown defect {self.defect!r}; expected one of {frames.DEFECTS}"
            )
        self.every = max(1, int(every))
        self.limit = max(1, int(limit))
        self.out_dir = Path(out_dir) if out_dir is not None else None
        #: Plates written so far, for the report and the dailies UI.
        self.plates: list[Path] = []

    def apply(self, farm: Farm, dt: float) -> None:
        if len(self.plates) >= self.limit or farm.ticks % self.every:
            return
        shot_id = self._pick_shot(farm)
        if shot_id is None:
            return

        shot = farm.film.get_shot(shot_id)
        job = farm.jobs[shot_id]
        frame_no = max(1, int(job.frames_done))
        path = frames.render_frame(shot_id, frame_no, self.defect, self.out_dir)
        self.plates.append(path)

        # Note the level and the wording: this is a clean completion line,
        # identical to the thousands around it. Nothing in the text or the
        # attributes hints that the plate is wrong, because on a real farm
        # nothing would.
        seconds = farm.mean_frame_seconds(shot_id) or 148.3
        self.emit(
            logging.INFO,
            f"[{shot.renderer}] INFO | frame {frame_no:04d} complete in "
            f"{seconds:.1f}s",
            shot_id=shot_id,
            node=job.node_name,
            renderer=shot.renderer,
            sequence=shot.sequence,
            artist=shot.artist,
        )

    def _pick_shot(self, farm: Farm) -> str | None:
        """The shot whose plates come back wrong; pinned on first use."""
        if self.shot_id is not None and self.shot_id in farm.jobs:
            return self.shot_id
        running = sorted(farm.jobs)
        if not running:
            return None
        if self.shot_id is None:
            self.shot_id = running[self._rng.randrange(len(running))]
            return self.shot_id if self.shot_id in farm.jobs else None
        # The pinned shot finished or died; follow it with the next one along so
        # the defect keeps appearing in dailies.
        return running[0]

    def describe(self) -> str:
        return (
            f"plates written with a '{self.defect}' defect; every metric stays "
            f"healthy"
        )


_FAULT_TYPES: dict[str, type[Fault]] = {
    OutOfMemoryFault.name: OutOfMemoryFault,
    LicenceStarvationFault.name: LicenceStarvationFault,
    TextureCacheMissFault.name: TextureCacheMissFault,
    CorruptFrameFault.name: CorruptFrameFault,
}


# --- entry points ----------------------------------------------------------


def inject(farm: Farm, fault_name: str, **kw: object) -> Fault:
    """Arm ``fault_name`` on ``farm``, taking effect on the next tick.

    Any fault already on the farm is cleared first: one farm, one fault, so the
    signatures in the dashboard stay attributable to a single cause.

    Args:
        farm: the farm to break.
        fault_name: one of :data:`FAULTS`.
        **kw: per-fault overrides — ``node=`` for oom, ``remaining=`` for
            licence starvation, ``ratio=`` for the cache, and
            ``shot_id=``/``defect=`` for corrupt frames.

    Raises:
        ValueError: if ``fault_name`` is not a known fault.
    """
    if fault_name not in _FAULT_TYPES:
        raise ValueError(f"unknown fault {fault_name!r}; expected one of {FAULTS}")
    clear(farm)
    fault = _FAULT_TYPES[fault_name](farm, **kw)
    farm.tick_hooks.append(fault.apply)
    farm._fault = fault  # noqa: SLF001 - the farm carries it, it does not use it
    return fault


def clear(farm: Farm) -> None:
    """Remove the active fault and undo everything it changed."""
    fault: Fault | None = getattr(farm, "_fault", None)
    if fault is None:
        return
    if fault.apply in farm.tick_hooks:
        farm.tick_hooks.remove(fault.apply)
    fault.revert(farm)
    farm._fault = None  # noqa: SLF001


def active(farm: Farm) -> Fault | None:
    """The fault currently armed on ``farm``, or ``None``."""
    return getattr(farm, "_fault", None)
