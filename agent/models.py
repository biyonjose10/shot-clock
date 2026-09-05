"""Which Gemini models the project uses, in one place.

Pinned, not aliased. `gemini-flash-latest` would quietly change underneath us
mid-build, and the demo has to behave the same on Tuesday as it did on Sunday.

A caution learned the hard way: `client.models.list()` advertises models the
key cannot actually call. A newly created project is treated as a "new user"
and is refused older models with a 404 that only appears at call time:

    This model models/gemini-2.5-flash is no longer available to new users.
    Please update your code to use models/gemini-3.6-flash

So the availability check that matters is a real request, not a listing.

Free-tier quotas are per model AND per day, and on the newest models they are
very small: gemini-3.6-flash allows 20 requests per day, which is roughly one
crew investigation. Retrying cannot recover a daily cap. Picking a slightly
older flash model buys a workable quota, and because the quota is per model,
moving to another one is a real reset rather than a dodge.
"""
from __future__ import annotations

import os

#: Tool-calling crew. Flash, not pro: this is many short tool-selection turns,
#: which flash handles well, and pro would multiply the cost of every run.
CREW_MODEL = os.environ.get("SHOT_CLOCK_CREW_MODEL", "gemini-3.5-flash")

#: Single-image classification with a constrained output schema.
VISION_MODEL = os.environ.get("SHOT_CLOCK_VISION_MODEL", "gemini-3.5-flash")

#: Narration for the demo video.
TTS_MODEL = os.environ.get("SHOT_CLOCK_TTS_MODEL", "gemini-2.5-flash-preview-tts")


def crew_llm():
    """The crew model, configured to survive free-tier rate limits.

    The free tier allows 5 requests per minute per model, and a tool-calling
    agent burns through that in one investigation: each tool round trip is
    another request. Without backoff the run dies partway with a 429 and takes
    the journal with it.

    Retrying is the right answer rather than downgrading the model, because
    waiting is free and the run is not latency-sensitive. A run that takes
    three minutes instead of one still costs nothing.
    """
    from google.adk.models import Gemini
    from google.genai import types

    return Gemini(
        model=CREW_MODEL,
        retry_options=types.HttpRetryOptions(
            attempts=8,
            initial_delay=8.0,
            max_delay=60.0,
            exp_base=1.5,
            # 429 is the free-tier quota; 503 is transient model overload.
            http_status_codes=[429, 503],
        ),
    )
