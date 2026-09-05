"""The render farm: 200 machines chewing through the film's shot list.

A render farm is a rack of identical-ish Linux boxes ("render nodes") managed by
a scheduler. A node takes one shot at a time, renders its frames one after
another, and reports back. Nodes fail in boring, repeatable ways: the render
runs out of memory on a heavy displacement frame, the renderer segfaults, or the
box drops off the network. The scheduler retries, and every retry burns time the
production does not have.

This module is a deterministic state machine over that world. ``Farm.tick(dt)``
advances simulated production time by ``dt`` seconds: it progresses frames,
completes or fails jobs, wobbles node health, and dispatches backlog shots onto
free nodes.

-----------------------------------------------------------------------------
CARDINALITY CONSTRAINT — read this before wiring up telemetry
-----------------------------------------------------------------------------
Grafana Cloud's free tier allows roughly 10k active series. A series is created
per unique combination of metric name and label values, so labels multiply.

The rule this module is built around:

    Metrics are labelled by `node` OR by `shot_id` — NEVER BOTH.

    * node-scoped series:  200 nodes x ~8 metrics  = ~1,600 series
    * shot-scoped series:   40 shots x ~6 metrics  =   ~240 series
    * farm-scoped series:  a couple of dozen unlabelled gauges

    Total stays comfortably under 2k. Crossing the two label sets would be
    200 x 40 = 8,000 combinations per metric and would blow the budget on the
    first metric alone.

That is why only ``MAX_IN_FLIGHT`` (40) shots may be active at once: the other
1,160 are a static backlog, counted in aggregate gauges and never given their
own series. It is also why this module exposes node state and shot state through
two separate accessors — ``node_states()`` and ``shot_states()`` — that share no
labels. ``shot_states()`` deliberately returns only in-flight shots. Read them
independently, emit them independently, and the series budget holds.
-----------------------------------------------------------------------------
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import StrEnum

from sim.film import FILM, Film, Shot, ShotStatus

# --- tuning constants ------------------------------------------------------

FARM_SEED = 90210

NODE_COUNT = 200

#: Hard cap on concurrently rendering shots. See the cardinality note above.
#: This is a telemetry budget, not a scheduler nicety — do not raise it without
#: recounting series.
MAX_IN_FLIGHT = 40

#: One tick is one minute of production time by default.
TICK_SECONDS = 60.0

#: Frames per simulated second for a nominal node on a complexity-1 plate.
#: A 134-frame average shot therefore takes roughly four simulated hours.
BASE_FRAME_RATE = 0.02

#: How long before the delivery date the simulation starts. Chosen so the farm
#: is running at high utilisation: comfortable enough to finish, tight enough
#: that failures push the tail of the shot list past the deadline.
PRODUCTION_WINDOW_DAYS = 6

#: Baseline probability, per node-hour, that a running job dies.
FAILURE_RATE_PER_HOUR = 0.012

#: A degraded node is far more likely to kill its job.
DEGRADED_FAILURE_MULTIPLIER = 8.0

#: A degraded node also renders far more slowly (thermal throttling, swapping).
DEGRADED_SPEED_MULTIPLIER = 0.35

#: Chance per node-hour of a healthy node degrading, and of a degraded node
#: dropping off the network entirely.
DEGRADE_RATE_PER_HOUR = 0.010
OFFLINE_RATE_PER_HOUR = 0.004
RECOVER_RATE_PER_HOUR = 0.35

#: Attempts a shot gets before a human has to look at it.
MAX_ATTEMPTS = 3

#: Completions needed before the at-risk projection fully trusts the farm's
#: measured throughput rather than its nominal one.
RISK_WARMUP_COMPLETIONS = 30

#: Memory fitted to each node, and how the fleet is split across the three
#: tiers. Heavy shots need >64 GB, so the cheap tier cannot take everything.
MEMORY_TIERS_GB: tuple[int, ...] = (64, 128, 256)
MEMORY_TIER_WEIGHTS: tuple[float, ...] = (0.45, 0.40, 0.15)

#: Floating renderer licences the studio owns. A render cannot start without
#: checking licences out of this pool, and hands them back when it ends. The
#: pool is shared by Arnold and Karma and is sized so a full farm *just* fits:
#: run everything flat out and the tail of the queue occasionally waits.
LICENCE_POOL_TOTAL = 140

#: Licences one job checks out. Heavy shots are rendered with more buckets in
#: parallel, and every bucket needs its own seat, so cost scales with
#: complexity: a 1-complexity plate takes 1 seat, a 10 takes LICENCE_MAX_PER_JOB.
LICENCE_COMPLEXITY_PER_SEAT = 3.0
LICENCE_MAX_PER_JOB = 4

#: A healthy farm-wide texture cache. Renders pull .tx texture tiles from a
#: shared cache in front of the asset store; a hit is a local read, a miss is a
#: refetch over the network before the bucket can shade.
TEXTURE_CACHE_BASELINE = 0.93

#: How hard the cache pulls back to baseline each tick, and the size of the
#: tick-to-tick jitter around it. Together these give a believable wobble.
TEXTURE_CACHE_PULL = 0.25
TEXTURE_CACHE_JITTER = 0.008

#: Frame-time multiplier per unit of hit-ratio lost. At the baseline ratio the
#: penalty is exactly 1.0, so a healthy cache leaves the calibrated frame rate
#: untouched; a collapse to 0.35 makes frames ~4.5x slower farm-wide.
TEXTURE_MISS_COST = 6.0


class NodeHealth(StrEnum):
    """What a node is currently capable of."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class FailureKind(StrEnum):
    """Why a job died. These are the faults the diagnostic agents reason about."""

    OUT_OF_MEMORY = "oom"
    RENDERER_CRASH = "renderer_crash"
    NODE_OFFLINE = "node_offline"


# --- entities --------------------------------------------------------------


@dataclass
class Node:
    """One render machine. Owns no shot detail beyond the id it is working on."""

    name: str
    memory_capacity_gb: int
    #: Per-machine speed factor; racks are never truly identical.
    speed: float
    health: NodeHealth = NodeHealth.HEALTHY
    current_shot_id: str | None = None
    memory_used_gb: float = 0.0
    jobs_completed: int = 0
    jobs_failed: int = 0
    busy_seconds: float = 0.0
    #: Multiplier on this node's resident memory. 1.0 is normal. A box with a
    #: leaking renderer plugin or a stuck cache flush sits above 1.0 and creeps
    #: toward its capacity; ``sim.faults`` drives this for the OOM scenario.
    memory_pressure: float = 1.0

    @property
    def is_free(self) -> bool:
        return self.current_shot_id is None and self.health is not NodeHealth.OFFLINE

    @property
    def speed_multiplier(self) -> float:
        """Effective throughput factor, health included. 0.0 means stopped."""
        if self.health is NodeHealth.OFFLINE:
            return 0.0
        if self.health is NodeHealth.DEGRADED:
            return self.speed * DEGRADED_SPEED_MULTIPLIER
        return self.speed


@dataclass
class Job:
    """One attempt at rendering one shot on one node."""

    shot_id: str
    node_name: str
    frames_total: int
    frames_done: float = 0.0
    attempt: int = 1
    started_at: float = 0.0
    #: Resident memory this attempt is currently using, in GB.
    memory_gb: float = 0.0
    #: Licences this attempt has checked out of the pool.
    licences: int = 1
    #: Simulated seconds this attempt has actually spent rendering. Excludes
    #: time parked waiting for a licence, so it is a true per-frame cost.
    render_seconds: float = 0.0

    @property
    def progress(self) -> float:
        if self.frames_total <= 0:
            return 1.0
        return min(1.0, self.frames_done / self.frames_total)


# --- telemetry-facing views ------------------------------------------------
# Two flat, frozen snapshots. The node view carries a `node` label and no shot
# label; the shot view carries a `shot_id` label and no node label except as a
# non-label attribute for the UI. Keep it that way.


@dataclass(frozen=True)
class NodeState:
    """Node-scoped snapshot. Label these series by ``node`` only."""

    node: str
    health: str
    busy: bool
    memory_capacity_gb: int
    memory_used_gb: float
    memory_utilization: float
    frames_per_hour: float
    jobs_completed: int
    jobs_failed: int
    busy_fraction: float


@dataclass(frozen=True)
class ShotState:
    """Shot-scoped snapshot for an in-flight shot. Label by ``shot_id`` only.

    ``node`` is present for the UI and for agent narration; it must not become a
    metric label alongside ``shot_id``.
    """

    shot_id: str
    sequence: str
    renderer: str
    artist: str
    status: str
    frames_total: int
    frames_done: int
    progress: float
    eta_seconds: float | None
    at_risk: bool
    attempt: int
    node: str | None
    #: Mean wall-clock seconds per completed frame on this attempt, 0.0 until
    #: the first frame lands. This is the number a supervisor quotes when they
    #: say a shot is "running slow", and the one a texture-cache collapse moves.
    mean_frame_seconds: float = 0.0


@dataclass(frozen=True)
class FarmSummary:
    """Farm-scoped aggregates. No per-entity labels at all."""

    sim_time: datetime
    delivery_deadline: datetime
    seconds_to_delivery: float
    nodes_total: int
    nodes_busy: int
    nodes_degraded: int
    nodes_offline: int
    shots_total: int
    shots_backlog: int
    shots_in_flight: int
    shots_complete: int
    shots_failed: int
    shots_at_risk: int
    frames_total: int
    frames_rendered: int
    frames_per_hour: float
    failures_total: int
    #: Floating renderer licences the studio owns, and how many are unclaimed.
    licences_total: int = LICENCE_POOL_TOTAL
    licences_available: int = LICENCE_POOL_TOTAL
    #: Farm-wide texture cache hit ratio, 0.0-1.0.
    texture_cache_hit_ratio: float = TEXTURE_CACHE_BASELINE
    #: Shots ready to render but held back for want of a node or a licence.
    queue_depth: int = 0


@dataclass(frozen=True)
class FailureEvent:
    """A job death, for logs and for the agents to correlate against."""

    sim_time: datetime
    shot_id: str
    node: str
    kind: str
    attempt: int
    frames_done: int
    frames_total: int
    terminal: bool


# --- the farm --------------------------------------------------------------


class Farm:
    """Deterministic render-farm state machine.

    Given the same ``seed``, ``film`` and the same sequence of ``tick`` calls,
    every observable value is identical run to run.
    """

    def __init__(
        self,
        film: Film | None = None,
        seed: int = FARM_SEED,
        node_count: int = NODE_COUNT,
        max_in_flight: int = MAX_IN_FLIGHT,
        start_time: datetime | None = None,
        licences_total: int = LICENCE_POOL_TOTAL,
    ) -> None:
        self.film = film if film is not None else FILM
        self.seed = seed
        self.max_in_flight = max_in_flight
        self._rng = random.Random(seed)
        # The cache wobble draws from its own stream so that adding or removing
        # a draw here cannot shift the node-health sequence, and vice versa.
        self._cache_rng = random.Random(seed ^ 0x7E27F00D)

        self.delivery_deadline = datetime.combine(
            self.film.delivery_date, time(23, 59, 59)
        )
        self.start_time = start_time or (
            self.delivery_deadline - timedelta(days=PRODUCTION_WINDOW_DAYS)
        )
        #: Simulated seconds since ``start_time``.
        self.clock: float = 0.0
        self.ticks: int = 0

        # The farm owns shot status; reset so repeated Farm() calls in one
        # process are still deterministic.
        for shot in self.film.shots:
            shot.status = ShotStatus.PENDING

        self.nodes: dict[str, Node] = self._build_nodes(node_count)
        self._node_order: list[str] = list(self.nodes)

        self.backlog: list[str] = [shot.shot_id for shot in self.film.shots]
        # Lazily rebuilt shot_id -> backlog position map; None means stale.
        self._backlog_index: dict[str, int] | None = None
        self.jobs: dict[str, Job] = {}  # shot_id -> in-flight job
        self.completed: list[str] = []
        self.failed: list[str] = []  # terminal failures only
        self.attempts: dict[str, int] = {}
        self.failures: list[FailureEvent] = []

        self.frames_rendered: float = 0.0
        self._frames_rendered_at_last_tick: float = 0.0
        self._frames_last_dt: float = 0.0

        #: Size of the floating licence pool. Shrink it and running jobs are
        #: reclaimed; ``sim.faults`` does exactly that for licence starvation.
        self.licences_total = licences_total
        #: Jobs parked mid-render because their licence was reclaimed. They keep
        #: their frame progress and resume when a seat frees up: a suspended
        #: render is stalled, not failed, and must not be counted as a failure.
        self.suspended: dict[str, Job] = {}

        #: Farm-wide texture cache hit ratio, and the value it is drifting to.
        #: Faults move the target; the ratio chases it over a few ticks rather
        #: than teleporting, because a cache degrades as it is invalidated.
        self.texture_cache_hit_ratio = TEXTURE_CACHE_BASELINE
        self.texture_cache_target = TEXTURE_CACHE_BASELINE

        #: Pre-tick observers, called as ``hook(farm, dt)`` before anything
        #: else moves. This is the seam ``sim.faults`` injects through, so the
        #: farm never has to know what a fault is.
        self.tick_hooks: list[Callable[["Farm", float], None]] = []

        # Mean estimated job length, used for queue-wait projections.
        self._mean_job_seconds = sum(
            self._estimated_job_seconds(shot) for shot in self.film.shots
        ) / max(1, self.film.shot_count)
        # Mean seats a job checks out, used to work out how many renders the
        # licence pool can sustain at once.
        self._mean_licences = sum(
            self.licences_for(shot) for shot in self.film.shots
        ) / max(1, self.film.shot_count)

        self._dispatch()

    # -- construction -------------------------------------------------------

    def _build_nodes(self, node_count: int) -> dict[str, Node]:
        nodes: dict[str, Node] = {}
        for index in range(node_count):
            name = f"rn-{index:03d}"
            capacity = self._rng.choices(MEMORY_TIERS_GB, MEMORY_TIER_WEIGHTS)[0]
            speed = round(self._rng.uniform(0.85, 1.15), 3)
            nodes[name] = Node(name=name, memory_capacity_gb=capacity, speed=speed)
        return nodes

    # -- time ---------------------------------------------------------------

    def now(self) -> datetime:
        """Current simulated wall-clock time."""
        return self.start_time + timedelta(seconds=self.clock)

    @property
    def seconds_to_delivery(self) -> float:
        """Simulated seconds left before the plates are due. Negative = late."""
        return (self.delivery_deadline - self.now()).total_seconds()

    # -- main loop ----------------------------------------------------------

    def tick(self, dt: float = TICK_SECONDS) -> None:
        """Advance simulated production time by ``dt`` seconds."""
        if dt <= 0:
            raise ValueError("dt must be positive")

        self.clock += dt
        self.ticks += 1
        self._frames_rendered_at_last_tick = self.frames_rendered

        for hook in list(self.tick_hooks):
            hook(self, dt)

        self._update_texture_cache()
        self._update_health(dt)
        self._reclaim_licences()
        self._advance_jobs(dt)
        self._dispatch()

        self._frames_last_dt = dt

    def run(self, ticks: int, dt: float = TICK_SECONDS) -> None:
        """Convenience: run ``ticks`` ticks of ``dt`` seconds each."""
        for _ in range(ticks):
            self.tick(dt)

    # -- health -------------------------------------------------------------

    def _update_health(self, dt: float) -> None:
        hours = dt / 3600.0
        for name in self._node_order:
            node = self.nodes[name]
            roll = self._rng.random()
            if node.health is NodeHealth.HEALTHY:
                if roll < DEGRADE_RATE_PER_HOUR * hours:
                    node.health = NodeHealth.DEGRADED
            elif node.health is NodeHealth.DEGRADED:
                if roll < OFFLINE_RATE_PER_HOUR * hours:
                    node.health = NodeHealth.OFFLINE
                    self._kill_job(node, FailureKind.NODE_OFFLINE)
                elif roll < (OFFLINE_RATE_PER_HOUR + RECOVER_RATE_PER_HOUR) * hours:
                    node.health = NodeHealth.HEALTHY
            else:  # OFFLINE — a tech power-cycles it eventually
                if roll < RECOVER_RATE_PER_HOUR * hours:
                    node.health = NodeHealth.HEALTHY

    def set_node_health(self, node_name: str, health: NodeHealth | str) -> None:
        """Force a node's health. The fault-injection hook for demos."""
        node = self.nodes[node_name]
        node.health = NodeHealth(health)
        if node.health is NodeHealth.OFFLINE:
            self._kill_job(node, FailureKind.NODE_OFFLINE)

    # -- licence pool -------------------------------------------------------
    # Arnold and Karma are licensed by floating seat: a central licence server
    # hands a render a token, the render holds it until it exits, and when the
    # pool is dry the next render blocks. Nothing about the frame gets slower —
    # the work simply does not start. That is the whole diagnostic signature.

    @staticmethod
    def licences_for(shot: Shot) -> int:
        """Seats one render of this shot checks out.

        Heavy shots are bucketed across more threads and each bucket wants its
        own seat, so cost rises with complexity and caps out at four.
        """
        seats = math.ceil(shot.complexity / LICENCE_COMPLEXITY_PER_SEAT)
        return max(1, min(LICENCE_MAX_PER_JOB, seats))

    @property
    def licences_in_use(self) -> int:
        """Seats currently checked out by running jobs."""
        return sum(job.licences for job in self.jobs.values())

    @property
    def licences_available(self) -> int:
        """Seats free in the pool. Never negative, even mid-reclaim."""
        return max(0, self.licences_total - self.licences_in_use)

    def _reclaim_licences(self) -> None:
        """Suspend running jobs until the pool is no longer oversubscribed.

        The pool only shrinks when something goes wrong with the licence server,
        and a render that loses its heartbeat does not crash: it blocks on the
        re-checkout with its frame buffer intact. Newest jobs are reclaimed
        first, so the renders closest to delivering are the ones left alone.
        """
        if self.licences_in_use <= self.licences_total:
            return
        # Newest first, shot_id breaking ties so the order is reproducible.
        victims = sorted(
            self.jobs.values(), key=lambda job: (-job.started_at, job.shot_id)
        )
        for job in victims:
            if self.licences_in_use <= self.licences_total:
                break
            self.suspend_job(job.shot_id)

    def suspend_job(self, shot_id: str) -> None:
        """Park a running job, keeping its progress, and free its node.

        This is not a failure: no ``FailureEvent`` is recorded and the node's
        ``jobs_failed`` is untouched. The shot goes back to PENDING and waits in
        :attr:`suspended` until a node and enough licences are free again.
        """
        job = self.jobs.pop(shot_id, None)
        if job is None:
            return
        node = self.nodes[job.node_name]
        node.current_shot_id = None
        node.memory_used_gb = 0.0
        self.film.get_shot(shot_id).status = ShotStatus.PENDING
        self.suspended[shot_id] = job

    # -- texture cache ------------------------------------------------------

    def _update_texture_cache(self) -> None:
        """Drift the farm-wide cache hit ratio toward its target.

        Real hit ratios wander: a new sequence's textures come in cold, a purge
        drops the working set. The ratio chases ``texture_cache_target`` with a
        little jitter, so a fault that moves the target shows up as a slide over
        several ticks rather than an impossible vertical step.
        """
        drift = (self.texture_cache_target - self.texture_cache_hit_ratio)
        noise = self._cache_rng.gauss(0.0, TEXTURE_CACHE_JITTER)
        ratio = self.texture_cache_hit_ratio + drift * TEXTURE_CACHE_PULL + noise
        self.texture_cache_hit_ratio = round(min(1.0, max(0.0, ratio)), 4)

    @property
    def texture_cache_penalty(self) -> float:
        """Frame-time multiplier from cache misses. 1.0 at a healthy ratio.

        Every miss is a texture tile refetched over the network before the
        bucket can shade, so the cost is linear in the hit ratio lost.
        """
        deficit = max(0.0, TEXTURE_CACHE_BASELINE - self.texture_cache_hit_ratio)
        return 1.0 + TEXTURE_MISS_COST * deficit

    # -- job progress -------------------------------------------------------

    def _advance_jobs(self, dt: float) -> None:
        hours = dt / 3600.0
        for shot_id in list(self.jobs):
            job = self.jobs.get(shot_id)
            if job is None:
                continue
            node = self.nodes[job.node_name]
            shot = self.film.get_shot(shot_id)

            speed = node.speed_multiplier
            if speed <= 0.0:
                continue  # offline node; the health pass already killed the job

            node.busy_seconds += dt
            job.render_seconds += dt

            # Memory creeps up over the render as geometry and textures page in,
            # and heavy frames spike. A spike past capacity is an OOM kill.
            spike = 1.0 + 0.35 * self._rng.random() * job.progress
            job.memory_gb = round(shot.memory_gb * spike * node.memory_pressure, 1)
            node.memory_used_gb = job.memory_gb
            if job.memory_gb > node.memory_capacity_gb:
                self._kill_job(node, FailureKind.OUT_OF_MEMORY)
                continue

            failure_rate = FAILURE_RATE_PER_HOUR
            if node.health is NodeHealth.DEGRADED:
                failure_rate *= DEGRADED_FAILURE_MULTIPLIER
            if self._rng.random() < failure_rate * hours:
                self._kill_job(node, FailureKind.RENDERER_CRASH)
                continue

            frames = self._frame_rate(node, shot) * dt
            job.frames_done += frames
            self.frames_rendered += min(
                frames, max(0.0, job.frames_total - (job.frames_done - frames))
            )

            if job.frames_done >= job.frames_total:
                self._complete_job(node, job, shot)

    def _frame_rate(self, node: Node, shot: Shot) -> float:
        """Frames per simulated second for this node/shot pairing.

        Texture cache misses are farm-wide, not per node: every render pulls
        from the same cache, so a cold cache slows the whole floor at once.
        """
        return (
            BASE_FRAME_RATE
            * node.speed_multiplier
            / (shot.complexity_factor * self.texture_cache_penalty)
        )

    def _complete_job(self, node: Node, job: Job, shot: Shot) -> None:
        job.frames_done = float(job.frames_total)
        shot.status = ShotStatus.COMPLETE
        node.jobs_completed += 1
        node.current_shot_id = None
        node.memory_used_gb = 0.0
        self.jobs.pop(job.shot_id, None)
        self.completed.append(job.shot_id)

    def _kill_job(self, node: Node, kind: FailureKind) -> None:
        """End the node's current job unsuccessfully; requeue or give up."""
        shot_id = node.current_shot_id
        if shot_id is None:
            return
        job = self.jobs.pop(shot_id, None)
        node.current_shot_id = None
        node.memory_used_gb = 0.0
        node.jobs_failed += 1
        if job is None:
            return

        shot = self.film.get_shot(shot_id)
        terminal = job.attempt >= MAX_ATTEMPTS
        if terminal:
            shot.status = ShotStatus.FAILED
            self.failed.append(shot_id)
        else:
            shot.status = ShotStatus.PENDING
            # Retries go to the front: a shot that has already burned time is
            # the most urgent thing on the farm.
            self.backlog.insert(0, shot_id)
            self._backlog_index = None

        self.failures.append(
            FailureEvent(
                sim_time=self.now(),
                shot_id=shot_id,
                node=node.name,
                kind=str(kind),
                attempt=job.attempt,
                frames_done=int(job.frames_done),
                frames_total=job.frames_total,
                terminal=terminal,
            )
        )

    # -- dispatch -----------------------------------------------------------

    def _dispatch(self) -> None:
        """Fill free nodes, up to the in-flight cap and the licence pool.

        Suspended renders are placed before anything new: a shot that already
        holds half its frames should get the next free seat, not queue behind a
        job that has not started.
        """
        for name in self._node_order:
            if len(self.jobs) >= self.max_in_flight:
                return
            node = self.nodes[name]
            if not node.is_free:
                continue
            if self._resume_on(node):
                continue
            shot = self._take_next_fitting_shot(node)
            if shot is None:
                continue
            self._start_job(node, shot)

    def _resume_on(self, node: Node) -> bool:
        """Put the oldest suspended job that fits this node back to work."""
        for shot_id in sorted(
            self.suspended, key=lambda sid: (self.suspended[sid].started_at, sid)
        ):
            job = self.suspended[shot_id]
            shot = self.film.get_shot(shot_id)
            if shot.memory_gb > node.memory_capacity_gb:
                continue
            if job.licences > self.licences_available:
                continue
            del self.suspended[shot_id]
            shot.status = ShotStatus.RENDERING
            node.current_shot_id = shot_id
            node.memory_used_gb = job.memory_gb or shot.memory_gb
            # Same attempt: a licence stall is not a retry, and the frames
            # already rendered are still on disk.
            job.node_name = node.name
            self.jobs[shot_id] = job
            return True
        return False

    def _take_next_fitting_shot(self, node: Node) -> Shot | None:
        """Pop the first backlog shot that fits this node's memory and the pool."""
        available = self.licences_available
        for index, shot_id in enumerate(self.backlog):
            shot = self.film.get_shot(shot_id)
            if shot.memory_gb > node.memory_capacity_gb:
                continue
            if self.licences_for(shot) > available:
                continue
            self.backlog.pop(index)
            self._backlog_index = None
            return shot
        return None

    def _start_job(self, node: Node, shot: Shot) -> None:
        attempt = self.attempts.get(shot.shot_id, 0) + 1
        self.attempts[shot.shot_id] = attempt
        shot.status = ShotStatus.RENDERING
        node.current_shot_id = shot.shot_id
        node.memory_used_gb = shot.memory_gb
        self.jobs[shot.shot_id] = Job(
            shot_id=shot.shot_id,
            node_name=node.name,
            frames_total=shot.frame_count,
            attempt=attempt,
            started_at=self.clock,
            memory_gb=shot.memory_gb,
            licences=self.licences_for(shot),
        )

    # -- progress, ETA and risk --------------------------------------------

    def shot_progress(self, shot_id: str) -> float:
        """Fraction of the shot's frames rendered, 0.0-1.0."""
        shot = self.film.get_shot(shot_id)
        if shot.status is ShotStatus.COMPLETE:
            return 1.0
        job = self.jobs.get(shot_id) or self.suspended.get(shot_id)
        return job.progress if job else 0.0

    def shot_eta_seconds(self, shot_id: str) -> float | None:
        """Simulated seconds until this shot finishes.

        For an in-flight shot this is the remaining render time on its node.
        For a backlog shot it includes the estimated wait for a free slot.
        ``None`` means stalled (its node is offline) — an important signal.
        ``0.0`` means done.
        """
        shot = self.film.get_shot(shot_id)
        if shot.status is ShotStatus.COMPLETE:
            return 0.0
        if shot.status is ShotStatus.FAILED:
            return None

        job = self.jobs.get(shot_id)
        if job is not None:
            node = self.nodes[job.node_name]
            rate = self._frame_rate(node, shot)
            if rate <= 0.0:
                return None
            return (job.frames_total - job.frames_done) / rate

        parked = self.suspended.get(shot_id)
        if parked is not None:
            return self._suspended_eta_seconds(shot, parked)

        return self._queued_eta_seconds(shot)

    def _suspended_eta_seconds(self, shot: Shot, job: Job) -> float:
        """Wait for a licence to free up, then finish the frames left.

        A suspended shot keeps its progress, so this is usually shorter than a
        cold queue wait — but it still slides past delivery once the pool has
        collapsed, which is what makes licence starvation show up as risk.
        """
        remaining = max(0.0, job.frames_total - job.frames_done)
        nominal_rate = BASE_FRAME_RATE / (
            shot.complexity_factor * self.texture_cache_penalty
        )
        return self._completion_interval_seconds() + remaining / nominal_rate

    def _queued_eta_seconds(self, shot: Shot) -> float:
        """Estimated wait for a slot, plus the shot's own render time.

        The wait is queue position times the farm's observed seconds-between-
        completions. Using the *observed* rate rather than a nominal one is what
        makes the at-risk count honest: retries, degraded nodes and offline
        boxes all slow completions down, and the projection follows.
        """
        position = self._backlog_position(shot.shot_id)
        return position * self._completion_interval_seconds() + (
            self._estimated_job_seconds(shot)
        )

    def _effective_slots(self) -> float:
        """Renders the farm can sustain at once: in-flight cap or licence pool.

        Whichever binds first is the real limit. Halve the pool and this halves
        with it, which is how a licence stall pushes ETAs out immediately rather
        than waiting for the observed completion rate to notice.
        """
        by_licence = self.licences_total / max(1e-9, self._mean_licences)
        return max(1.0, min(float(self.max_in_flight), by_licence))

    def _completion_interval_seconds(self) -> float:
        """Observed simulated seconds between shot completions, farm-wide."""
        # The ideal: every slot the farm can actually run is busy, each chewing
        # through an average shot at the current cache hit ratio.
        nominal = (
            self._mean_job_seconds
            * self.texture_cache_penalty
            / self._effective_slots()
        )
        if not self.completed or self.clock <= 0.0:
            return nominal
        observed = self.clock / len(self.completed)
        # A handful of completions is a noisy sample, so fade the observed rate
        # in over the first RISK_WARMUP_COMPLETIONS shots.
        weight = min(1.0, len(self.completed) / RISK_WARMUP_COMPLETIONS)
        return weight * observed + (1.0 - weight) * nominal

    def _backlog_position(self, shot_id: str) -> int:
        """Index of a shot in the backlog; len(backlog) if it is not queued.

        Cached because the risk projection asks for all 1200 shots at once.
        """
        if self._backlog_index is None:
            self._backlog_index = {
                queued_id: index for index, queued_id in enumerate(self.backlog)
            }
        return self._backlog_index.get(shot_id, len(self.backlog))

    def _estimated_job_seconds(self, shot: Shot) -> float:
        """How long this shot should take on a nominal healthy node.

        Includes the current texture cache penalty: with a cold cache the same
        shot genuinely takes longer, and the projection has to say so.
        """
        return (
            shot.frame_count
            * shot.complexity_factor
            * self.texture_cache_penalty
            / BASE_FRAME_RATE
        )

    def is_at_risk(self, shot_id: str) -> bool:
        """True if this shot is projected to finish after the delivery date.

        A terminally failed shot is always at risk: nobody is rendering it.
        """
        shot = self.film.get_shot(shot_id)
        if shot.status is ShotStatus.COMPLETE:
            return False
        if shot.status is ShotStatus.FAILED:
            return True
        eta = self.shot_eta_seconds(shot_id)
        if eta is None:
            return True
        return eta > self.seconds_to_delivery

    def at_risk_shot_ids(self) -> list[str]:
        """Every shot projected to miss delivery, in shot-list order.

        Note this walks all 1200 shots. It feeds a single aggregate gauge — do
        not emit one series per at-risk shot.
        """
        return [
            shot.shot_id for shot in self.film.shots if self.is_at_risk(shot.shot_id)
        ]

    # -- telemetry accessors -----------------------------------------------
    # node_states() and shot_states() share no label. See the module docstring.

    def node_states(self) -> list[NodeState]:
        """Node-scoped snapshot of all 200 nodes. Label by ``node`` only."""
        elapsed = max(1.0, self.clock)
        states: list[NodeState] = []
        for name in self._node_order:
            node = self.nodes[name]
            shot_id = node.current_shot_id
            rate = 0.0
            if shot_id is not None:
                rate = self._frame_rate(node, self.film.get_shot(shot_id)) * 3600.0
            states.append(
                NodeState(
                    node=node.name,
                    health=str(node.health),
                    busy=shot_id is not None,
                    memory_capacity_gb=node.memory_capacity_gb,
                    memory_used_gb=round(node.memory_used_gb, 1),
                    memory_utilization=round(
                        node.memory_used_gb / node.memory_capacity_gb, 4
                    ),
                    frames_per_hour=round(rate, 3),
                    jobs_completed=node.jobs_completed,
                    jobs_failed=node.jobs_failed,
                    busy_fraction=round(min(1.0, node.busy_seconds / elapsed), 4),
                )
            )
        return states

    def node_state(self, node_name: str) -> NodeState:
        for state in self.node_states():
            if state.node == node_name:
                return state
        raise KeyError(node_name)

    def shot_states(self) -> list[ShotState]:
        """Shot-scoped snapshot of the in-flight shots only (at most 40).

        Label by ``shot_id`` only. The backlog is never returned here; it is
        reported as counts in :meth:`summary`.
        """
        states: list[ShotState] = []
        for shot_id in sorted(self.jobs):
            states.append(self._shot_state(shot_id))
        return states

    def mean_frame_seconds(self, shot_id: str) -> float:
        """Mean simulated seconds per frame completed on this shot's attempt.

        Rendering time only — a job parked waiting for a licence does not get
        slower, it just stops, so the licence stall must not show up here.
        Returns 0.0 until the first whole frame lands.
        """
        job = self.jobs.get(shot_id) or self.suspended.get(shot_id)
        if job is None or job.frames_done < 1.0:
            return 0.0
        return round(job.render_seconds / job.frames_done, 2)

    def _shot_state(self, shot_id: str) -> ShotState:
        shot = self.film.get_shot(shot_id)
        job = self.jobs.get(shot_id) or self.suspended.get(shot_id)
        return ShotState(
            shot_id=shot.shot_id,
            sequence=shot.sequence,
            renderer=shot.renderer,
            artist=shot.artist,
            status=str(shot.status),
            frames_total=shot.frame_count,
            frames_done=int(job.frames_done) if job else 0,
            progress=round(self.shot_progress(shot_id), 4),
            eta_seconds=self.shot_eta_seconds(shot_id),
            at_risk=self.is_at_risk(shot_id),
            attempt=job.attempt if job else self.attempts.get(shot_id, 0),
            node=job.node_name if job else None,
            mean_frame_seconds=self.mean_frame_seconds(shot_id),
        )

    def shot_state(self, shot_id: str) -> ShotState:
        """Snapshot for any shot, in flight or not (for the UI, not for metrics)."""
        return self._shot_state(shot_id)

    @property
    def queue_depth(self) -> int:
        """Renders that are ready to go but held back for want of a resource.

        Demand is however many of the in-flight slots there is work to fill —
        suspended jobs and backlog shots both count. Whatever the farm failed
        to actually start is blocked on a free node or a licence. At a healthy
        baseline this sits near zero; it is the licence pool's alarm bell.
        """
        wanted = min(
            self.max_in_flight,
            len(self.jobs) + len(self.suspended) + len(self.backlog),
        )
        return max(0, wanted - len(self.jobs))

    def summary(self) -> FarmSummary:
        """Farm-scoped aggregates. Safe to emit as unlabelled gauges."""
        busy = sum(1 for n in self.nodes.values() if n.current_shot_id is not None)
        degraded = sum(
            1 for n in self.nodes.values() if n.health is NodeHealth.DEGRADED
        )
        offline = sum(1 for n in self.nodes.values() if n.health is NodeHealth.OFFLINE)
        frames_per_hour = 0.0
        if self._frames_last_dt > 0:
            delta = self.frames_rendered - self._frames_rendered_at_last_tick
            frames_per_hour = delta * 3600.0 / self._frames_last_dt

        return FarmSummary(
            sim_time=self.now(),
            delivery_deadline=self.delivery_deadline,
            seconds_to_delivery=round(self.seconds_to_delivery, 1),
            nodes_total=len(self.nodes),
            nodes_busy=busy,
            nodes_degraded=degraded,
            nodes_offline=offline,
            shots_total=self.film.shot_count,
            # Suspended renders are PENDING again, so they belong in the
            # backlog count; otherwise a licence stall would make shots vanish
            # from every counter at once.
            shots_backlog=len(self.backlog) + len(self.suspended),
            shots_in_flight=len(self.jobs),
            shots_complete=len(self.completed),
            shots_failed=len(self.failed),
            shots_at_risk=len(self.at_risk_shot_ids()),
            frames_total=self.film.frame_count,
            frames_rendered=int(self.frames_rendered),
            frames_per_hour=round(frames_per_hour, 2),
            failures_total=len(self.failures),
            licences_total=self.licences_total,
            licences_available=self.licences_available,
            texture_cache_hit_ratio=self.texture_cache_hit_ratio,
            queue_depth=self.queue_depth,
        )

    def recent_failures(self, limit: int = 20) -> list[FailureEvent]:
        """The most recent job deaths, newest last."""
        return self.failures[-limit:]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Farm t={self.now():%Y-%m-%d %H:%M} nodes={len(self.nodes)} "
            f"in_flight={len(self.jobs)} complete={len(self.completed)} "
            f"backlog={len(self.backlog)}>"
        )
