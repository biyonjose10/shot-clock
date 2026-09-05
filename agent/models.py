"""Which Gemini models the project uses, and how the free quota is spread.

Pinned, not aliased. `gemini-flash-latest` would quietly change underneath us
mid-build, and the demo has to behave the same on Tuesday as it did on Sunday.

Two things about the free tier that are only discoverable by hitting them:

1. `client.models.list()` advertises models the key cannot call. A newly
   created project counts as a "new user" and is refused older models with a
   404 raised only at call time:

       This model models/gemini-2.5-flash is no longer available to new users.
       Please update your code to use models/gemini-3.6-flash

   The availability check that matters is a real request, not a listing.

2. The free quota is `GenerateRequestsPerDayPerProjectPerModel` and it is
   **20 requests per day, per model**. One crew investigation is 5-15 requests,
   so a single model is roughly one run a day, and retrying cannot recover a
   daily cap. Because the quota is per model, giving each crew member its own
   model multiplies the usable budget by the size of the pool. That is quota
   Google granted for this project, not quota being circumvented -- which is
   why the rotation is across models rather than across new projects.
"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "journals" / ".model_usage.json"

#: Models verified callable on this project by an actual request. Full flash
#: models first; tool selection degrades on the lite tier.
MODEL_POOL: list[str] = [
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-3.8-flash",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite",
]

#: Free-tier daily allowance per model, used to plan rotation.
DAILY_BUDGET = 20

#: Each crew member gets its own model so their quotas do not collide: one run
#: draws on four separate budgets instead of exhausting a single one.
ROLE_MODELS: dict[str, str] = {
    "scout": "gemini-3.5-flash",
    "gaffer": "gemini-3.6-flash",
    "producer": "gemini-3.7-flash",
    "first_ad": "gemini-3.8-flash",
    "vision": "gemini-3-flash-preview",
}

#: Vertex AI and AI Studio do NOT expose the same models, and the difference is
#: not a version skew -- it is disjoint. On this project's free AI Studio key
#: gemini-2.5-flash is refused ("no longer available to new users") while the
#: 3.x line works; on Vertex it is the exact reverse, and every 3.x id returns
#: "Publisher model ... was not found". A build that only ever ran against AI
#: Studio therefore 404s for every visitor the moment it is deployed on Vertex.
#: Verified by real generateContent calls against both backends.
VERTEX_CREW_MODEL = os.environ.get("SHOT_CLOCK_VERTEX_MODEL", "gemini-2.5-flash")
VERTEX_VISION_MODEL = os.environ.get("SHOT_CLOCK_VERTEX_MODEL", "gemini-2.5-flash")


def on_vertex() -> bool:
    return os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").upper() == "TRUE"


CREW_MODEL = os.environ.get("SHOT_CLOCK_CREW_MODEL") or (
    VERTEX_CREW_MODEL if on_vertex() else MODEL_POOL[0]
)
VISION_MODEL = os.environ.get("SHOT_CLOCK_VISION_MODEL") or (
    VERTEX_VISION_MODEL if on_vertex() else ROLE_MODELS["vision"]
)
TTS_MODEL = os.environ.get("SHOT_CLOCK_TTS_MODEL", "gemini-2.5-flash-preview-tts")


# --- paid key guard --------------------------------------------------------
# Standing instruction: the paid key is never spent without being asked first.
# Encoding it here means it cannot happen through a forgotten env var.

class PaidKeyBlocked(RuntimeError):
    """Refused to spend the paid key without an explicit opt-in."""


def assert_not_paid_key() -> None:
    if os.environ.get("SHOT_CLOCK_ALLOW_PAID") == "1":
        return
    key = (os.environ.get("GOOGLE_API_KEY") or "").strip()
    paid = (os.environ.get("GOOGLE_API_KEY_PAID") or "").strip()
    if paid and key and key == paid:
        raise PaidKeyBlocked(
            "GOOGLE_API_KEY is the PAID key. Refusing to spend it without "
            "permission. If this is intended, re-run with "
            "SHOT_CLOCK_ALLOW_PAID=1."
        )


# --- usage ledger ----------------------------------------------------------
def _load() -> dict:
    if not LEDGER.exists():
        return {}
    try:
        data = json.loads(LEDGER.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a corrupt ledger is not worth failing on
        return {}
    return data if data.get("date") == date.today().isoformat() else {}


def record_use(model: str, calls: int = 1) -> None:
    data = _load() or {"date": date.today().isoformat(), "models": {}}
    data.setdefault("models", {})
    data["models"][model] = data["models"].get(model, 0) + calls
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(data, indent=2), encoding="utf-8")


def usage_today() -> dict[str, int]:
    return dict(_load().get("models", {}))


def budget_report() -> str:
    used = usage_today()
    lines = ["  model                        calls used  free calls left"]
    for m in MODEL_POOL:
        u = used.get(m, 0)
        lines.append(f"  {m:26s} {u:10d}  {max(DAILY_BUDGET - u, 0):14d}")
    return "\n".join(lines)


def model_for(role: str) -> str:
    """The model for this role, skipping any believed exhausted today."""
    if on_vertex():
        # Vertex bills per call rather than rationing per day, so there is no
        # quota to rotate around -- and the 3.x ids the rotation uses do not
        # exist there at all.
        return CREW_MODEL
    preferred = os.environ.get("SHOT_CLOCK_CREW_MODEL") or ROLE_MODELS.get(role)
    used = usage_today()
    if preferred and used.get(preferred, 0) < DAILY_BUDGET:
        return preferred
    for candidate in MODEL_POOL:
        if used.get(candidate, 0) < DAILY_BUDGET:
            return candidate
    # Everything is spent. Return the preferred one so the failure is a clear
    # 429 naming the model, rather than a confusing silent fallback.
    return preferred or MODEL_POOL[0]


def crew_llm(role: str = "scout"):
    """The model for `role`, configured to survive free-tier rate limits.

    Backoff handles the per-minute quota; it cannot rescue the daily cap, which
    is what the per-role rotation is for.
    """
    from google.adk.models import Gemini
    from google.genai import types

    assert_not_paid_key()
    model = model_for(role)
    return Gemini(
        model=model,
        retry_options=types.HttpRetryOptions(
            attempts=6,
            initial_delay=8.0,
            max_delay=60.0,
            exp_base=1.5,
            # 429 covers the per-minute quota; 503 is transient overload.
            http_status_codes=[429, 503],
        ),
    )
