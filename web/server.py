"""The Shot Clock war room: one page, three panels, no credentials required.

    ./.venv/Scripts/python.exe -m web.server
    (or: uvicorn web.server:app --port 8000)

What this process holds:

* one :class:`sim.farm.Farm`, ticked by a background task so the shot board is
  alive the moment a judge opens the page. The farm needs no network and no
  keys — it is a deterministic state machine over 200 render nodes.
* one :class:`agent.journal.Journal`, the live event spine. Everything the crew
  does is recorded there; ``/api/events`` fans it out to browsers as SSE.

DEMO MODE (``POST /api/demo``) replays a recorded journal into that live
journal at its original cadence, so the console renders a real incident even
with no Grafana stack and no model key in the environment. If no recording
exists on disk yet, a scripted stand-in is synthesised (and clearly labelled as
synthetic in the response) so the UI is demonstrable today.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agent import journal as journal_mod
from agent.journal import Journal
from sim.farm import Farm, ShotState
from web import scripted_demo

log = logging.getLogger("shot_clock.web")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# Local development reads .env; on Cloud Run the same names arrive as real
# environment variables and secrets, and load_dotenv is a no-op.
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:  # noqa: BLE001 - dotenv is a convenience, never a dependency
    pass

STATIC_DIR = HERE / "static"
INDEX_HTML = HERE / "templates" / "index.html"

#: Simulated production seconds advanced per wall-clock second. The farm thinks
#: in production time (a shot takes hours); the console has to be watchable, so
#: ten production minutes pass per real second — the same ratio ``sim.main``
#: uses when it streams telemetry to Grafana.
SIM_SECONDS_PER_SECOND = 600.0

#: Wall-clock seconds between farm ticks.
TICK_INTERVAL = 1.0

#: Production time the farm is fast-forwarded through at startup, so the board
#: opens with shots part-rendered rather than 40 empty progress bars.
WARMUP_SIM_SECONDS = 6 * 3600.0

#: Replay speed for DEMO MODE unless the caller asks for another.
DEFAULT_DEMO_SPEED = 1.4

#: A shot that will land with less than this share of the remaining window to
#: spare is "at risk": it is still projected to make it, but one retry or one
#: degraded node takes it past the date. Below zero it is simply late.
AT_RISK_SLACK = 0.15


#: ``Journal.record(kind, actor, **payload)`` takes the event kind and actor as
#: named parameters, so a payload key called "kind" or "actor" is a TypeError
#: rather than data. Nothing recorded live can hit this — the crew calls
#: ``record`` the same way — but a hand-written journal can, and it must not be
#: allowed to end a replay halfway through.
RESERVED_PAYLOAD_KEYS = ("kind", "actor")


def safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Copy a payload with any reserved key renamed rather than dropped."""
    if not any(key in payload for key in RESERVED_PAYLOAD_KEYS):
        return payload
    out = dict(payload)
    for key in RESERVED_PAYLOAD_KEYS:
        if key in out:
            out[f"{key}_"] = out.pop(key)
    return out


class Console:
    """Everything the two endpoints share. One instance, created at startup."""

    def __init__(self) -> None:
        self.farm = Farm()
        # A Journal per process. The UI clears its panels whenever it sees a
        # run_start, so several runs can share one stream without confusion.
        self.journal = Journal()
        self.board: dict[str, Any] = {}
        self.demo_task: asyncio.Task[None] | None = None
        self.demo_source: str | None = None

    # -- farm ---------------------------------------------------------------
    def warm_up(self) -> None:
        """Advance the farm to a believable mid-production state."""
        ticks = int(WARMUP_SIM_SECONDS / SIM_SECONDS_PER_SECOND)
        self.farm.run(ticks, dt=SIM_SECONDS_PER_SECOND)
        self.board = build_board(self.farm)

    async def tick_forever(self) -> None:
        """Keep production time moving. Cancelled on shutdown."""
        while True:
            await asyncio.sleep(TICK_INTERVAL)
            try:
                self.farm.tick(SIM_SECONDS_PER_SECOND * TICK_INTERVAL)
                # Built once per tick rather than per request: the risk
                # projection walks all 1200 shots and there is no reason to
                # redo it for every poll from every open browser tab.
                self.board = build_board(self.farm)
            except Exception:  # pragma: no cover - the board must never die
                log.exception("farm tick failed")

    # -- demo ---------------------------------------------------------------
    async def replay_into_live(self, path: Path, speed: float) -> None:
        """Re-record a journal file into the live journal at its own cadence.

        Replaying through ``record`` rather than straight to the socket means
        DEMO MODE and LIVE mode are the same code path from the browser's point
        of view, and a client that joins halfway still gets the backlog.
        """
        last = ""
        try:
            async for event in journal_mod.replay(path, speed=speed):
                try:
                    self.journal.record(event.kind, event.actor, **safe_payload(event.payload))
                    last = event.kind
                except Exception:  # pragma: no cover
                    # One malformed event must never truncate the replay: the
                    # rest of the run is still worth showing.
                    log.exception("could not re-record event seq=%s", event.seq)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover
            log.exception("demo replay failed")
        # The console leaves its button on "Replaying" until a run_end arrives.
        # A journal that lacks one -- or a replay that fell over partway --
        # would strand the UI mid-run, so close the run out either way.
        if last and last != journal_mod.RUN_END:
            self.journal.record(journal_mod.RUN_END, "system")


CONSOLE = Console()


# --- shot board -------------------------------------------------------------


def classify(state: ShotState, seconds_to_delivery: float) -> str:
    """Bucket one shot into the four colours the board uses.

    ``Farm.is_at_risk`` is a hard binary — projected to land after the delivery
    date or not. The board wants one more step of nuance than that, because a
    supervisor treats "will miss" and "will only just make it" differently:

        failed    exhausted its retries; a human has to look at it
        late      projected to finish after the delivery date, or stalled
        at-risk   projected to make it, but with almost no slack left
        on-track  comfortable
    """
    if state.status == "failed":
        return "failed"
    if state.status == "complete":
        return "on-track"
    if state.at_risk or state.eta_seconds is None:
        return "late"
    if seconds_to_delivery <= 0:
        return "late"
    slack = (seconds_to_delivery - state.eta_seconds) / seconds_to_delivery
    return "at-risk" if slack < AT_RISK_SLACK else "on-track"


def build_board(farm: Farm) -> dict[str, Any]:
    """Snapshot the whole board: in-flight shots, aggregates, sequence roll-up.

    Only the 40 in-flight shots are sent as individual records. The other 1160
    are counted per sequence — that is the same constraint the telemetry lives
    under (see the cardinality note in ``sim/farm.py``) and it keeps the DOM to
    a few dozen nodes instead of 1200.
    """
    summary = farm.summary()
    left = summary.seconds_to_delivery
    at_risk_ids = set(farm.at_risk_shot_ids())

    shots: list[dict[str, Any]] = []
    counts = {"on-track": 0, "at-risk": 0, "late": 0, "failed": 0}
    for state in farm.shot_states():
        shot = farm.film.get_shot(state.shot_id)
        risk = classify(state, left)
        counts[risk] += 1
        shots.append(
            {
                "shot_id": state.shot_id,
                "sequence": state.sequence,
                "sequence_code": shot.sequence_code,
                "artist": state.artist,
                "renderer": state.renderer,
                "status": state.status,
                "risk": risk,
                "frames_done": state.frames_done,
                "frames_total": state.frames_total,
                "progress": state.progress,
                "eta_seconds": state.eta_seconds,
                "attempt": state.attempt,
                "node": state.node,
                "mean_frame_seconds": state.mean_frame_seconds,
                "complexity": shot.complexity,
                "memory_gb": shot.memory_gb,
            }
        )

    # Per-sequence roll-up over the whole 1200-shot film, so the backlog reads
    # as a real body of work rather than a single number. One pass over the
    # shot list, bucketed by the two-letter sequence code.
    tally: dict[str, dict[str, int]] = {
        seq.code: {"complete": 0, "failed": 0, "rendering": 0, "at_risk": 0}
        for seq in farm.film.sequences
    }
    for shot in farm.film.shots:
        bucket = tally[shot.sequence_code]
        if shot.status in bucket:
            bucket[shot.status] += 1
        if shot.shot_id in at_risk_ids:
            bucket["at_risk"] += 1

    sequences: list[dict[str, Any]] = []
    for seq in farm.film.sequences:
        bucket = tally[seq.code]
        done = bucket["complete"] + bucket["failed"] + bucket["rendering"]
        sequences.append(
            {
                "code": seq.code,
                "name": seq.name,
                "description": seq.description,
                "total": seq.shot_count,
                "complete": bucket["complete"],
                "failed": bucket["failed"],
                "rendering": bucket["rendering"],
                "at_risk": bucket["at_risk"],
                "pending": seq.shot_count - done,
            }
        )

    return {
        "film": {
            "title": farm.film.title,
            "delivery_date": farm.film.delivery_date.isoformat(),
            "shots_total": farm.film.shot_count,
            "frames_total": farm.film.frame_count,
        },
        "sim_time": summary.sim_time.isoformat(),
        "delivery_deadline": summary.delivery_deadline.isoformat(),
        "seconds_to_delivery": left,
        # The browser interpolates the countdown between polls; it needs to
        # know how fast production time is running to do that.
        "sim_rate": SIM_SECONDS_PER_SECOND,
        "farm": {
            "nodes_total": summary.nodes_total,
            "nodes_busy": summary.nodes_busy,
            "nodes_degraded": summary.nodes_degraded,
            "nodes_offline": summary.nodes_offline,
            "frames_rendered": summary.frames_rendered,
            "frames_total": summary.frames_total,
            "frames_per_hour": summary.frames_per_hour,
            "failures_total": summary.failures_total,
            "licences_total": summary.licences_total,
            "licences_available": summary.licences_available,
            "texture_cache_hit_ratio": round(summary.texture_cache_hit_ratio, 4),
            "queue_depth": summary.queue_depth,
        },
        "totals": {
            "shots_total": summary.shots_total,
            "backlog": summary.shots_backlog,
            "in_flight": summary.shots_in_flight,
            "complete": summary.shots_complete,
            "failed": summary.shots_failed,
            "at_risk": summary.shots_at_risk,
        },
        "in_flight_counts": counts,
        "shots": shots,
        "sequences": sequences,
    }


# --- app --------------------------------------------------------------------


async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    CONSOLE.warm_up()
    ticker = asyncio.create_task(CONSOLE.tick_forever())
    try:
        yield
    finally:
        ticker.cancel()
        if CONSOLE.demo_task is not None:
            CONSOLE.demo_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ticker


app = FastAPI(title="Shot Clock", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    # no-store so a judge reloading the page never gets yesterday's console.
    return FileResponse(INDEX_HTML, headers={"Cache-Control": "no-store"})


@app.get("/api/shots")
async def shots() -> JSONResponse:
    """Current shot board. Polled; the event feed is the streaming half."""
    return JSONResponse(CONSOLE.board or build_board(CONSOLE.farm))


@app.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    """Server-Sent Events over the live journal.

    One ``data:`` line of JSON per journal Event, in the order recorded. A
    client that connects late is sent everything already in the journal first
    (``Journal.stream`` does that for us), so a browser refresh mid-run rebuilds
    the whole trace instead of joining blind.
    """

    async def source() -> AsyncIterator[str]:
        queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=1000)

        async def pump() -> None:
            try:
                async for event in CONSOLE.journal.stream():
                    await queue.put(event.to_json())
            finally:
                await queue.put(None)

        task = asyncio.create_task(pump())
        try:
            yield ": open\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # A comment line keeps proxies and load balancers from
                    # deciding an idle stream is a dead one.
                    yield ": keep-alive\n\n"
                    continue
                if payload is None:
                    return
                yield f"data: {payload}\n\n"
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return StreamingResponse(
        source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Tell nginx-style proxies not to buffer, or nothing shows up until
            # the run is over.
            "X-Accel-Buffering": "no",
        },
    )


def _is_real_complete_run(path: Path) -> bool:
    """Is this journal a finished recording of a real crew run?

    Two ways a journal fails to qualify, both of which have actually shipped:

    1. It stops partway. Runs die on a 429 all the time on the free tier, and
       resuming one leaves a fragment on disk deliberately. Replaying it gives
       an investigation that halts at Scout and never resets the console.

    2. It is scripted. A stand-in journal was once written under a live-*.jsonl
       filename, so excluding by filename let it be picked and announced as
       `synthetic: false` -- the site replaying a script while the README says
       it replays a real run. A journal declares what it is in its run_start
       payload, so trust that rather than the name it was saved under.
    """
    try:
        events = journal_mod.read(path)
    except Exception:  # noqa: BLE001 - an unreadable journal is not a candidate
        return False
    if not events or events[-1].kind != journal_mod.RUN_END:
        return False
    head = events[0]
    if head.kind == journal_mod.RUN_START and head.payload.get("mode") == "demo":
        return False
    return True


def _demo_journal(requested: str | None) -> tuple[Path, bool]:
    """Pick the journal DEMO MODE replays. Returns (path, synthetic?)."""
    if requested:
        path = Path(requested)
        if not path.is_absolute():
            path = ROOT / path
        if path.exists():
            return path, False

    # A curated recording of a real crew run ships as demo-*.jsonl. Anything
    # else already on disk beats falling back to the scripted stand-in; the
    # journal this process is currently writing is not a candidate.
    #
    # A candidate must be a COMPLETE run. Journals of runs that died partway
    # are ordinary on the free tier -- a 429 ends a run mid-Gaffer and leaves
    # its fragment on disk, and resuming a run leaves one there deliberately.
    # Picking one replays an investigation that stops dead at Scout and never
    # resets the console, which is worse than the honest scripted stand-in.
    candidates = [
        p
        for p in journal_mod.JOURNAL_DIR.glob("*.jsonl")
        if p != CONSOLE.journal.path
        and p.stat().st_size > 0
        and not p.name.startswith(scripted_demo.FILENAME_STEM)
        and _is_real_complete_run(p)
    ]
    candidates.sort(
        key=lambda p: (p.name.startswith("demo-"), p.stat().st_mtime), reverse=True
    )
    if candidates:
        return candidates[0], False
    return scripted_demo.ensure_journal(), True


@app.post("/api/demo")
async def demo(request: Request) -> JSONResponse:
    """Start DEMO MODE: replay a journal into the live feed.

    Body (all optional): ``{"speed": 1.4, "path": "journals/demo-oom.jsonl"}``.
    Starting a demo while one is running replaces it; the UI resets on the
    run_start event that the new replay opens with.
    """
    body: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        body = await request.json()

    speed = float(body.get("speed") or DEFAULT_DEMO_SPEED)
    path, synthetic = _demo_journal(body.get("path"))

    if CONSOLE.demo_task is not None and not CONSOLE.demo_task.done():
        CONSOLE.demo_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await CONSOLE.demo_task

    CONSOLE.demo_source = str(path)
    CONSOLE.demo_task = asyncio.create_task(CONSOLE.replay_into_live(path, speed))
    return JSONResponse(
        {
            "mode": "demo",
            "journal": path.name,
            "events": len(journal_mod.read(path)),
            "speed": speed,
            # Honest labelling: a synthetic journal is a scripted stand-in, not
            # a recording of a real crew run.
            "synthetic": synthetic,
        }
    )


@app.get("/api/status")
async def status() -> JSONResponse:
    """Small health/mode probe, handy when checking a deployment."""
    running = CONSOLE.demo_task is not None and not CONSOLE.demo_task.done()
    return JSONResponse(
        {
            "ok": True,
            "run_id": CONSOLE.journal.run_id,
            "events_recorded": len(CONSOLE.journal.events),
            "demo_running": running,
            "demo_source": CONSOLE.demo_source,
            "sim_time": CONSOLE.board.get("sim_time"),
        }
    )


@app.post("/api/tech-check")
async def tech_check() -> JSONResponse:
    """The one genuinely live model call this service makes.

    Everything else on this page replays a recorded journal, which is why a
    visitor cannot run up a bill. This endpoint really does send a rendered
    frame to Gemini through Vertex AI, because a contest rule requires Google
    Cloud to be called at runtime and a pure replay never calls anything.

    Capped per day in agent.live_check. Past the cap the last real verdict is
    returned, labelled as cached.
    """
    import asyncio as _asyncio

    from agent import live_check

    try:
        # The model call is blocking, so keep it off the event loop or the SSE
        # stream stalls for every other viewer while one judge waits.
        result = await _asyncio.to_thread(live_check.run)
    except Exception as exc:  # noqa: BLE001 - surface it, never 500 the page
        return JSONResponse(
            {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "remaining": live_check.remaining(),
            },
            status_code=200,
        )
    return JSONResponse({"ok": True, **result})


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
