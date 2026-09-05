"""The one genuinely live model call the deployed app makes.

The war room replays a recorded journal, which is deliberate: it is instant,
deterministic, has no cold start, and cannot be made to spend money by whoever
opens the link. But a contest rule requires Google Cloud to be *called at
runtime*, and a pure replay never calls anything.

So exactly one live path survives into the deployment: a judge presses "run the
tech check" and Gemini genuinely inspects a rendered frame, through Vertex AI.
One image, one constrained response, a fraction of a cent.

It is capped anyway. A public URL is a public URL, and a demo that can be
hammered into a bill is a bad demo. Past the cap the last real verdict is
returned and labelled as cached, so the page still works and nobody is charged
for the hundredth click.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "journals" / ".live_check.json"

#: Live inspections per day, across every visitor. Generous for judging, far
#: too small to matter on a bill.
DAILY_LIMIT = int(os.environ.get("SHOT_CLOCK_LIVE_CHECK_LIMIT", "40"))

#: The plate a judge inspects. Deterministic, and the defect is genuinely in
#: the image rather than asserted in a caption.
DEFAULT_SHOT = "RC_0410"
DEFAULT_FRAME = 112
DEFAULT_DEFECT = "fireflies"

_lock = threading.Lock()


def _load() -> dict[str, Any]:
    if not STATE.exists():
        return {}
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return data if data.get("date") == date.today().isoformat() else {}


def _save(data: dict[str, Any]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def remaining() -> int:
    return max(DAILY_LIMIT - int(_load().get("used", 0)), 0)


def run(
    shot_id: str = DEFAULT_SHOT,
    frame_no: int = DEFAULT_FRAME,
    defect: str | None = DEFAULT_DEFECT,
) -> dict[str, Any]:
    """Inspect a frame with Gemini, or return the last verdict if capped."""
    with _lock:
        state = _load()
        used = int(state.get("used", 0))
        cached = state.get("last")

        if used >= DAILY_LIMIT and cached:
            return {**cached, "live": False, "cached": True, "remaining": 0}

        # Imported here, not at module scope: the war room must start and serve
        # the replay even with no model credentials configured at all.
        from agent.models import VISION_MODEL
        from agent.vision import tech_check
        from sim.frames import render_frame

        path = render_frame(shot_id, frame_no, defect)
        verdict = tech_check(path)

        result = {
            "shot_id": shot_id,
            "frame": frame_no,
            "verdict": verdict.verdict,
            "confidence": verdict.confidence,
            "evidence": verdict.evidence,
            "region": verdict.region,
            "deliverable": verdict.deliverable,
            "image": "/static/frames/" + path.name,
            # Read the real value rather than restating a default: this string
            # is shown to whoever pressed the button, and a wrong model name is
            # worse than none.
            "model": VISION_MODEL,
            "via": "vertex-ai"
            if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").upper() == "TRUE"
            else "ai-studio",
        }
        _save({"date": date.today().isoformat(), "used": used + 1, "last": result})
        return {**result, "live": True, "cached": False, "remaining": DAILY_LIMIT - used - 1}
