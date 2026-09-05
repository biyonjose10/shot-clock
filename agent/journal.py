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


class Journal:
    """Append-only run record with a live fan-out for the war room UI."""

    def __init__(self, run_id: str | None = None, path: Path | None = None) -> None:
        self.run_id = run_id or f"live-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
        self.path = path or JOURNAL_DIR / f"{self.run_id}.jsonl"
        self._started = time.monotonic()
        self._seq = 0
        self._subscribers: list[asyncio.Queue[Event | None]] = []
        self._events: list[Event] = []

    # -- writing -----------------------------------------------------------
    def record(self, kind: str, actor: str = "system", **payload: Any) -> Event:
        """Append one event, persist it, and fan it out to any listeners."""
        self._seq += 1
        event = Event(
            kind=kind,
            actor=actor,
            offset=round(time.monotonic() - self._started, 3),
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


async def replay(
    path: Path, speed: float = 1.0, max_gap: float = 4.0
) -> AsyncIterator[Event]:
    """Yield a recorded run at its original cadence.

    Args:
        path: the journal to replay.
        speed: 2.0 runs it twice as fast. The demo script sets this so the
            whole incident fits inside three minutes.
        max_gap: never wait longer than this between events, however long the
            original pause was. A live run may stall on a slow tool call;
            the video cannot.
    """
    events = read(path)
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
