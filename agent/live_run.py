"""Let a visitor start a real crew run, capped, and stream it as it happens.

Everything else the deployed app serves is a replay. That is the right default:
it is instant, deterministic, and cannot be made to spend money by whoever
opens the link. But a replay can only ever *show* that agents did the work, and
the honest objection to any observability-agent demo is "is this actually
running, or is it a recording?"

So this is the answer to that objection: press the button and four Gemini
agents genuinely investigate the farm, through the Grafana MCP server, into the
same journal the war room already renders. Nothing about the page changes --
the events arrive on the same stream, in the same shapes.

It is capped hard, and deliberately harder than it needs to be:

* A daily allowance, defaulting to a handful of runs.
* One run at a time across the whole process. A crew run is minutes long and
  four concurrent ones would multiply the bill by four for no extra proof.
* The counter is per-instance, like the tech check's, so `deploy.sh` pinning
  max-instances low is what makes the daily number mean something. That is
  stated rather than glossed: see the note in agent/live_check.py.

Past the cap the button explains itself and the replay is offered instead.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "journals" / ".live_run.json"

#: Real crew runs per day per instance. A run is roughly 24 model requests, so
#: this is small change on Vertex -- but a public URL is a public URL, and the
#: point of a demo is not to find out how large a bill it can generate.
DAILY_LIMIT = int(os.environ.get("SHOT_CLOCK_LIVE_RUN_LIMIT", "6"))

#: A crew run takes minutes. Nobody waits that long twice, and two at once
#: proves nothing the first does not.
_slot = threading.Semaphore(1)
_lock = threading.Lock()

#: The farm-liveness answer is cached: /api/status is polled by every viewer
#: and a Prometheus round trip per poll would be silly.
FARM_CHECK_EVERY = 60.0
_farm_checked_at = 0.0
_farm_was_live = False


class Busy(RuntimeError):
    """A crew run is already in progress."""


class Spent(RuntimeError):
    """Today's allowance of live runs is gone."""


def _load() -> dict[str, Any]:
    if not STATE.exists():
        return {}
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a corrupt counter is not worth failing on
        return {}
    return data if data.get("date") == date.today().isoformat() else {}


def _spend() -> int:
    with _lock:
        data = _load() or {"date": date.today().isoformat(), "used": 0}
        used = int(data.get("used", 0))
        if used >= DAILY_LIMIT:
            raise Spent(f"{DAILY_LIMIT} live runs already started today")
        data["used"] = used + 1
        data["date"] = date.today().isoformat()
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return DAILY_LIMIT - used - 1


def remaining() -> int:
    return max(DAILY_LIMIT - int(_load().get("used", 0)), 0)


def farm_available() -> bool:
    """Is there a farm to investigate? Cheap, cached briefly, no model call."""
    global _farm_checked_at, _farm_was_live
    now = time.monotonic()
    if now - _farm_checked_at < FARM_CHECK_EVERY:
        return _farm_was_live
    try:
        from agent.readings import farm_is_reporting

        _farm_was_live = farm_is_reporting()
    except Exception:  # noqa: BLE001 - no credentials, no live run
        _farm_was_live = False
    _farm_checked_at = now
    return _farm_was_live


def available() -> bool:
    """Could a live run start right now?"""
    return (
        remaining() > 0
        and _slot._value > 0  # noqa: SLF001 - reading, not taking
        and farm_available()
    )


async def start(journal) -> dict[str, Any]:
    """Run the crew into ``journal``. Raises Busy or Spent rather than queueing.

    The orchestrator is imported here rather than at module scope so that the
    war room still starts, and still serves the replay, on a deployment with no
    model credentials at all.
    """
    if not _slot.acquire(blocking=False):
        raise Busy("a crew run is already in progress")
    try:
        left = _spend()
        from agent.orchestrator import investigate

        journal.record(
            "caption",
            "system",
            text="LIVE — four Gemini agents are investigating the farm now",
            duration_ms=5200,
        )
        await investigate(jnl=journal)
        return {"ok": True, "live": True, "remaining": left}
    finally:
        _slot.release()


def blocked_reason() -> str | None:
    """Why a live run cannot start, phrased for whoever pressed the button."""
    if _slot._value == 0:  # noqa: SLF001
        return (
            "A live crew run is already in progress. It takes a few minutes; "
            "the replay below is the same investigation, recorded."
        )
    if remaining() <= 0:
        return (
            f"Today's {DAILY_LIMIT} live runs are used up. The replay below is "
            f"a real recorded run, and the tech check still calls Vertex AI."
        )
    if not farm_available():
        return (
            "The render farm is not reporting to Grafana right now, so there "
            "is nothing live to investigate. The replay below is a real "
            "recorded run against a farm that was."
        )
    return None
