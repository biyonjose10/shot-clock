"""Which Gemini models the project uses, in one place.

Pinned, not aliased. `gemini-flash-latest` would quietly change underneath us
mid-build, and the demo has to behave the same on Tuesday as it did on Sunday.

A caution learned the hard way: `client.models.list()` advertises models the
key cannot actually call. A newly created project is treated as a "new user"
and is refused older models with a 404 that only appears at call time:

    This model models/gemini-2.5-flash is no longer available to new users.
    Please update your code to use models/gemini-3.6-flash

So the availability check that matters is a real request, not a listing.
"""
from __future__ import annotations

import os

#: Tool-calling crew. Flash, not pro: this is many short tool-selection turns,
#: which flash handles well, and pro would multiply the cost of every run.
CREW_MODEL = os.environ.get("SHOT_CLOCK_CREW_MODEL", "gemini-3.6-flash")

#: Single-image classification with a constrained output schema.
VISION_MODEL = os.environ.get("SHOT_CLOCK_VISION_MODEL", "gemini-3.6-flash")

#: Narration for the demo video.
TTS_MODEL = os.environ.get("SHOT_CLOCK_TTS_MODEL", "gemini-2.5-flash-preview-tts")
