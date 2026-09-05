"""The tech check: look at the frame, not at the job status.

Telemetry can only tell you a render *succeeded*. It cannot tell you the plate
came back black, or crawling with fireflies, or missing a texture map. Those are
render failures that report exit code 0, and every VFX house pays people to sit
in dailies and catch them by eye.

That gap is not a gap in our instrumentation, it is structural: the renderer has
no idea the image is wrong. So Shot Clock pulls the rendered frame and has
Gemini inspect it, then hands the verdict back to the crew to correlate against
the node and the log lines from the same window.

The model is deliberately NOT told which defect to look for, or even that a
defect exists. A checker that has been told the answer is not a checker. It must
be able to return `clean` for a good plate, and false positives matter as much
as misses -- a tech check that flags every frame is worse than none.
"""
from __future__ import annotations

import os
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from google import genai
from google.genai import types

from agent.models import VISION_MODEL as DEFAULT_MODEL

TECH_CHECK_PROMPT = """
You are a VFX dailies technical check. You are looking at one rendered frame
from a feature film shot. Your job is to decide whether this frame is
deliverable, or whether the render has failed in a way the render farm's job
status would not have caught.

Judge only technical integrity. Do not comment on artistic choices, framing,
composition, colour grading or subject matter. A dark, moody, minimal or
stylised plate is not a defect.

Known render failure modes, for reference:
  - black_frame       the image is empty or near-black; the render died or
                      wrote an empty buffer
  - fireflies         isolated blown-out white pixels scattered across the
                      image, including over solid geometry and ground planes.
                      Caused by undersampled indirect light. Distinguish from
                      stars: stars appear only in sky, never on top of
                      foreground objects
  - missing_texture   a large flat region of uniform colour, typically magenta,
                      with no shading or detail, where a surface should be
                      textured
  - clean             no technical defect; the frame is deliverable

Return your verdict. If the frame is deliverable, say so; do not invent a
defect. Cite what you actually see as evidence, and give the approximate image
region if there is a defect.
""".strip()


#: Structured output schema. Constrained output keeps the verdict machine
#: readable so the crew can branch on it, rather than parsing prose.
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "required": ["verdict", "confidence", "evidence"],
    "properties": {
        "verdict": {
            "type": "STRING",
            "enum": ["clean", "black_frame", "fireflies", "missing_texture", "other"],
        },
        "confidence": {"type": "NUMBER"},
        "evidence": {
            "type": "STRING",
            "description": "What you actually see, in one or two sentences.",
        },
        "region": {
            "type": "STRING",
            "description": "Where in the frame, if there is a defect. Empty if clean.",
        },
        "deliverable": {"type": "BOOLEAN"},
    },
}


@dataclass
class Verdict:
    """A tech check result, ready to journal or show in the war room."""

    frame: str
    verdict: str
    confidence: float
    evidence: str
    region: str
    deliverable: bool

    @property
    def is_defect(self) -> bool:
        return self.verdict != "clean"

    def headline(self) -> str:
        if not self.is_defect:
            return f"{Path(self.frame).name}: clean, deliverable"
        return (
            f"{Path(self.frame).name}: {self.verdict.replace('_', ' ')} "
            f"({self.confidence:.0%} confidence)"
        )


@lru_cache(maxsize=1)
def _client() -> genai.Client:
    """Honour the same Vertex/AI-Studio switch the rest of the project uses.

    Cached: a fresh Client per call gets garbage collected mid-flight and its
    underlying httpx session closed, which surfaces as
    "Cannot send a request, as the client has been closed."
    """
    if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").upper() == "TRUE":
        return genai.Client(
            vertexai=True,
            project=os.environ["GOOGLE_CLOUD_PROJECT"],
            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )
    return genai.Client(api_key=os.environ["GOOGLE_API_KEY"])


def tech_check(frame_path: str | Path, model: str = DEFAULT_MODEL) -> Verdict:
    """Inspect one rendered frame and return a structured verdict.

    Args:
        frame_path: PNG written by ``sim.frames.render_frame``.
        model: override the vision model.

    Raises:
        FileNotFoundError: if the frame does not exist.
    """
    path = Path(frame_path)
    if not path.exists():
        raise FileNotFoundError(path)

    response = _client().models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(data=path.read_bytes(), mime_type="image/png"),
            TECH_CHECK_PROMPT,
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
            # Deterministic verdicts: the same plate must not be clean on one
            # take and defective on the next, or the demo is not reproducible.
            temperature=0.0,
        ),
    )

    data = json.loads(response.text)
    return Verdict(
        frame=str(path),
        verdict=data["verdict"],
        confidence=float(data.get("confidence", 0.0)),
        evidence=data.get("evidence", ""),
        region=data.get("region", ""),
        deliverable=bool(data.get("deliverable", data["verdict"] == "clean")),
    )
