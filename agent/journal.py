"""Record every step of a crew run, and replay it deterministically.

This is the spine the rest of the project hangs off, and it exists for one
reason: the demo video is a single continuous take with no editing, and four
LLM agents driving a 72-tool MCP server do not hit the same beats twice.

So every run is journaled to JSONL as it happens -- each agent turn, each
Grafana tool call and its raw response, each vision verdict, each costing.
That gives two modes over exactly the same UI and the same renderer:

    LIVE    real agents, real MCP calls. What a judge hits at the hosted URL.
    DEMO    replays a real recorded journal at its original cadence.

DEMO MODE is a replay of a genuine live run, not a fabrication, and the README
says so plainly. The journal doubles as the debugging record all week: when a
sub-agent picks the wrong tool, the evidence is already on disk.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Iterator

ROOT = Path(__file__).resolve().parents[1]
JOURNAL_DIR = ROOT / "journals"

#: Event kinds. Kept small and explicit; the UI switches on these.
RUN_START = "run_start"
AGENT_START = "agent_start"
AGENT_THOUGHT = "agent_thought"
TOOL_CALL = "tool_call"
TOOL_RESULT = "tool_result"
VISION_VERDICT = "vision_verdict"
COSTING = "costing"
WRITE_BACK = "write_back"
CAPTION = "caption"
RUN_END = "run_end"


@dataclass
class Event:
    """One thing that happened during a run.

    Attributes:
        offset: seconds since the run began. Replay uses this, not wall clock,
            so a journal recorded last Tuesday replays at its original pace.
        actor: which crew member -- scout, gaffer, producer, first_ad, system.
    """

    kind: str
    actor: str
    offset: float
    payload: dict[str, Any] = field(default_factory=dict)
    seq: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @staticmethod
    def from_json(line: str) -> "Event":
        return Event(**json.loads(line))


#: Seconds to insert between the last event of an interrupted run and the
#: first event of its continuation. The free-tier quota is per model per day,
#: so a run that dies on a 429 is resumed the next day; recording the real
#: eighteen-hour gap would claim the Producer sat thinking overnight.
RESUME_GAP = 2.0


class Journal:
    """Append-only run record with a live fan-out for the war room UI."""

    def __init__(
        self,
        run_id: str | None = None,
        path: Path | None = None,
        resume: bool = False,
    ) -> None:
        self.run_id = run_id or f"live-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
        self.path = path or JOURNAL_DIR / f"{self.run_id}.jsonl"
        self._started = time.monotonic()
        self._seq = 0
        self._offset_base = 0.0
        self._subscribers: list[asyncio.Queue[Event | None]] = []
        self._events: list[Event] = []
        if resume and self.path.exists():
            # Continue an interrupted run: adopt its events, its sequence
            # numbers and its timeline, then append as though nothing stopped.
            self._events = read(self.path)
            if self._events:
                self._seq = self._events[-1].seq
                self._offset_base = self._events[-1].offset + RESUME_GAP

    # -- writing -----------------------------------------------------------
    def record(self, kind: str, actor: str = "system", **payload: Any) -> Event:
        """Append one event, persist it, and fan it out to any listeners."""
        self._seq += 1
        event = Event(
            kind=kind,
            actor=actor,
            offset=round(self._offset_base + time.monotonic() - self._started, 3),
            payload=payload,
            seq=self._seq,
        )
        self._events.append(event)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(event.to_json() + "\n")
        self._publish(event)
        return event

    def _publish(self, event: Event | None) -> None:
        for queue in list(self._subscribers):
            # put_nowait rather than await: recording must never block the run,
            # and a UI that has stopped reading is not worth stalling for.
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    def close(self) -> None:
        self._publish(None)

    # -- reading -----------------------------------------------------------
    @property
    def events(self) -> list[Event]:
        return list(self._events)

    async def stream(self) -> AsyncIterator[Event]:
        """Subscribe to live events. Replays what already happened first."""
        queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=1000)
        for event in self._events:
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)
        self._subscribers.append(queue)
        try:
            while True:
                event = await queue.get()
                if event is None:
                    return
                yield event
        finally:
            if queue in self._subscribers:
                self._subscribers.remove(queue)


def read(path: Path) -> list[Event]:
    """Load a journal from disk."""
    with path.open(encoding="utf-8") as fh:
        return [Event.from_json(line) for line in fh if line.strip()]


def latest(pattern: str = "*.jsonl") -> Path | None:
    """Most recently modified journal matching ``pattern``."""
    candidates = sorted(
        JOURNAL_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True
    )
    return candidates[0] if candidates else None


#: Seconds each section of the recorded take should occupy on screen, keyed by
#: the crew member whose caption opens it. These are the narration's beats.
#:
#: A real investigation does not pace itself for an audience. This run spent 66
#: seconds in the Scout -- a third of the whole take watching queries scroll --
#: and reached the tech check, the beat the film exists for, with 13 seconds
#: left for it. Re-pacing fixes that WITHOUT touching the record: not one event
#: is added, removed, reordered or edited, and every number on screen is the
#: one the agent read. Only the rate of playback changes, which `speed` and
#: `max_gap` were already doing.
#: Set from MEASURED voiceover audio, not from the script's section headings.
#: The written timings assumed 135 words a minute; Gemini TTS delivers about
#: 119, and far less on short sentences, so the spoken take ran 22 seconds
#: longer than the estimate against a 3:00 pass/fail limit. Fitting the picture
#: to the voice is the way round that cannot drift: the voice is fixed audio,
#: the picture is re-paceable.
DEMO_SECTION_SECONDS: dict[str, float] = {
    "scout": 17.0,
    "gaffer": 30.0,
    "vision": 33.0,
    "producer": 26.0,
    "first_ad": 29.0,
}


#: Seconds of stillness to leave AFTER a section's last event, before the next
#: section opens. The tech check needs it: the verdict arrives when Gemini
#: answers, which is the last thing that happens in that section, so scaling
#: alone put the frame on screen one second before the Producer wiped it. The
#: narration asks to hold on the plate, and this is that hold.
SECTION_HOLD_SECONDS: dict[str, float] = {"vision": 14.0}


def direct(
    events: list[Event], sections: dict[str, float] | None = None
) -> list[Event]:
    """Re-pace a run to the narration's beats, keeping every event intact.

    Sections are delimited by caption events, and each is stretched or
    compressed onto its target duration by scaling the offsets inside it,
    less any hold reserved for stillness at the end. Order is preserved and
    content is untouched.
    """
    sections = sections or DEMO_SECTION_SECONDS
    if not events:
        return events
    marks = [i for i, e in enumerate(events) if e.kind == CAPTION]
    if not marks:
        return events

    out = [Event(e.kind, e.actor, e.offset, dict(e.payload), e.seq) for e in events]
    cursor = 0.0
    for n, start in enumerate(marks):
        end = marks[n + 1] if n + 1 < len(marks) else len(out)
        actor = out[start].actor
        span_start = out[start].offset
        span_end = out[end].offset if end < len(out) else out[-1].offset
        actual = max(span_end - span_start, 0.0)
        target = sections.get(actor, actual)
        hold = min(SECTION_HOLD_SECONDS.get(actor, 0.0), max(target - 1.0, 0.0))
        playable = max(target - hold, 0.0)
        scale = (playable / actual) if actual > 0.01 else 0.0
        for i in range(start, end):
            out[i].offset = round(cursor + (out[i].offset - span_start) * scale, 3)
        cursor += target
    # Anything before the first caption (run_start) opens the take.
    for i in range(marks[0]):
        out[i].offset = 0.0
    return out


async def replay(
    path: Path,
    speed: float = 1.0,
    max_gap: float = 4.0,
    directed: bool = False,
) -> AsyncIterator[Event]:
    """Yield a recorded run at its original cadence.

    Args:
        path: the journal to replay.
        speed: 2.0 runs it twice as fast. The demo script sets this so the
            whole incident fits inside three minutes.
        max_gap: never wait longer than this between events, however long the
            original pause was. A live run may stall on a slow tool call;
            the video cannot.
        directed: re-pace the sections onto the narration's beats. Raises the
            gap ceiling, because the point of the stretch is the hold on the
            rendered frame -- clamping that back to four seconds would undo
            exactly the beat it exists to create.
    """
    events = read(path)
    if directed:
        events = direct(events)
        max_gap = max(max_gap, 14.0)
    previous = 0.0
    for event in events:
        gap = min((event.offset - previous) / max(speed, 0.01), max_gap)
        if gap > 0:
            await asyncio.sleep(gap)
        previous = event.offset
        yield event


def summarise(path: Path) -> dict[str, Any]:
    """Quick stats for a journal, for picking which recording to ship."""
    events = read(path)
    if not events:
        return {"path": str(path), "events": 0}
    kinds: dict[str, int] = {}
    tools: list[str] = []
    for event in events:
        kinds[event.kind] = kinds.get(event.kind, 0) + 1
        if event.kind == TOOL_CALL:
            tools.append(str(event.payload.get("tool", "?")))
    return {
        "path": str(path),
        "events": len(events),
        "duration_s": round(events[-1].offset, 1),
        "kinds": kinds,
        "tool_calls": tools,
    }


def iter_journals() -> Iterator[Path]:
    yield from sorted(JOURNAL_DIR.glob("*.jsonl"))
