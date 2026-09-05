"""Emit one distributed trace per rendered frame.

A frame is a small pipeline, and it is the natural unit of a trace:

    render_frame
      |- scene_load      open the USD/Houdini scene, resolve references
      |- texture_fetch   pull .tx tiles, the step a cold cache punishes
      |- render          the actual ray tracing
      |- denoise         OptiX/OIDN pass
      |- write           write the EXR to shared storage

Spans are backdated with explicit start and end timestamps so a frame that took
110 simulated seconds shows as 110 seconds in Tempo, even though the simulator
advanced through it in a few seconds of wall clock. Without that the waterfall
would be meaningless.

The per-stage split is where the fault signatures become legible: a cold
texture cache inflates ``texture_fetch`` specifically, rather than spreading
evenly across the frame, which is what makes a trace worth consulting at all.
"""
from __future__ import annotations

import random
import time
from typing import Iterable

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

#: Fraction of a frame's wall time each stage takes on a healthy farm.
STAGE_SHARE = {
    "scene_load": 0.08,
    "texture_fetch": 0.12,
    "render": 0.62,
    "denoise": 0.11,
    "write": 0.07,
}

#: Never emit more than this many frame traces per tick. A 200-node farm can
#: finish hundreds of frames in a simulated ten minutes, and Tempo does not
#: need every one of them to tell the story.
MAX_SPANS_PER_TICK = 12


class FrameTracer:
    """Turn per-tick frame completions into spans.

    The farm reports cumulative ``frames_done`` per shot, so completions are
    recovered by diffing against the previous tick.
    """

    def __init__(self, seed: int = 5150) -> None:
        self._tracer = trace.get_tracer("shot-clock.frames")
        self._seen: dict[str, int] = {}
        self._rng = random.Random(seed)

    def observe(self, farm) -> int:
        """Emit spans for frames completed since the last call. Returns count."""
        completions = list(self._completions(farm))
        if len(completions) > MAX_SPANS_PER_TICK:
            completions = self._rng.sample(completions, MAX_SPANS_PER_TICK)
        cache_ratio = farm.summary().texture_cache_hit_ratio
        for shot, frame_no in completions:
            self._emit(shot, frame_no, cache_ratio)
        return len(completions)

    def _completions(self, farm) -> Iterable[tuple[object, int]]:
        for shot in farm.shot_states():
            previous = self._seen.get(shot.shot_id, shot.frames_done)
            self._seen[shot.shot_id] = shot.frames_done
            for frame_no in range(previous + 1, shot.frames_done + 1):
                yield shot, frame_no

    def _emit(self, shot, frame_no: int, cache_ratio: float) -> None:
        duration = shot.mean_frame_seconds or 90.0
        # Jitter so the waterfall does not look synthetic.
        duration *= self._rng.uniform(0.85, 1.15)

        end_ns = time.time_ns()
        start_ns = end_ns - int(duration * 1e9)

        attributes = {
            "shot.id": shot.shot_id,
            "shot.sequence": shot.sequence,
            "shot.artist": shot.artist,
            "render.renderer": shot.renderer,
            "render.frame": frame_no,
            # The trace is the one signal that carries shot AND node together,
            # because a trace is not a time series and costs no cardinality.
            "render.node": shot.node or "unassigned",
        }

        parent = self._tracer.start_span(
            "render_frame", kind=SpanKind.SERVER, start_time=start_ns, attributes=attributes
        )
        try:
            cursor = start_ns
            for stage, share in STAGE_SHARE.items():
                stage_seconds = duration * self._stage_share(stage, share, cache_ratio)
                stage_end = cursor + int(stage_seconds * 1e9)
                with trace.use_span(parent, end_on_exit=False):
                    child = self._tracer.start_span(stage, start_time=cursor)
                    if stage == "texture_fetch" and cache_ratio < 0.6:
                        child.set_attribute("texture.cache_hit_ratio", cache_ratio)
                        child.set_status(
                            Status(StatusCode.ERROR, "texture cache thrashing")
                        )
                    child.end(end_time=stage_end)
                cursor = stage_end
        finally:
            parent.end(end_time=end_ns)

    @staticmethod
    def _stage_share(stage: str, share: float, cache_ratio: float) -> float:
        """A cold cache lands on texture_fetch, not evenly across the frame."""
        if stage == "texture_fetch" and cache_ratio < 0.9:
            return min(0.75, share * (1.0 + (0.9 - cache_ratio) * 8.0))
        return share
