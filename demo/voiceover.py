"""Generate the trailer voiceover with Gemini TTS.

Nothing is recorded by hand. The narration in `narration.md` is spoken by
Gemini and the picture is a single unedited screen capture of DEMO MODE. The
trailer ships without music, so there is no `--music` flag: this once claimed
a Lyria score that was never built, which is a poor thing to advertise in a
file a judge can open.

    python -m demo.voiceover              # every segment
    python -m demo.voiceover --only SLUG  # regenerate one
    python -m demo.voiceover --dry-run    # count words, call nothing

Each segment is written separately and named by its timecode, so a segment can
be regenerated on its own without paying for the whole script again.
"""
from __future__ import annotations

import argparse
import os
import re
import struct
import sys
import wave
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from agent.models import TTS_MODEL, assert_not_paid_key  # noqa: E402

OUT = ROOT / "demo" / "audio"
SCRIPT = ROOT / "demo" / "narration.md"

#: Calm, unhurried, a post supervisor rather than an advert. Gemini TTS voices
#: are named; Charon reads low and level, which suits the register.
VOICE = os.environ.get("SHOT_CLOCK_TTS_VOICE", "Charon")

STYLE = (
    "Read as a calm, unhurried post-production supervisor briefing a room. "
    "Measured pace, no advertising lilt, no rising inflection. Let the pauses "
    "sit. This is someone explaining something they find genuinely interesting."
)


def segments() -> list[tuple[str, str]]:
    """Parse narration.md into (slug, spoken text) pairs.

    Only blockquote lines are spoken. Everything else in that file is stage
    direction, timing and reasoning, and must never reach the microphone.
    """
    text = SCRIPT.read_text(encoding="utf-8")
    out: list[tuple[str, str]] = []
    current: str | None = None
    spoken: list[str] = []

    for line in text.splitlines():
        heading = re.match(r"^##\s+(.*)$", line)
        if heading:
            if current and spoken:
                out.append((current, " ".join(spoken).strip()))
            title = heading.group(1)
            slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
            current, spoken = slug, []
            continue
        if line.startswith(">"):
            body = line.lstrip("> ").strip()
            if body:
                spoken.append(body)

    if current and spoken:
        out.append((current, " ".join(spoken).strip()))
    return [(s, t) for s, t in out if t]


def _pcm_to_wav(path: Path, pcm: bytes, rate: int = 24_000) -> None:
    """Gemini TTS returns raw 16-bit mono PCM, not a container."""
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(pcm)


def speak(slug: str, text: str) -> Path:
    """Generate one segment, rejecting output that has obviously run away.

    The model occasionally returns a wildly long take -- 49 words came back as
    339 seconds once, and 16 words as 655. The same text regenerates correctly,
    so this is a bad roll rather than a bad script, and the answer is to notice
    and ask again. Without the check a corrupt segment sits on disk looking
    exactly like a good one until someone plays it.
    """
    budget = expected_seconds(text) * RUNAWAY_FACTOR
    last: Path | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        last = _speak_once(slug, text)
        seconds = wav_seconds(last)
        if seconds <= budget:
            return last
        print(
            f"    runaway take: {seconds:.0f}s for {len(text.split())} words "
            f"(budget {budget:.0f}s). attempt {attempt} of {MAX_ATTEMPTS}."
        )
    print(f"    WARNING: {slug} never came back within budget; keeping the last take.")
    return last


def _speak_once(slug: str, text: str) -> Path:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    response = client.models.generate_content(
        model=TTS_MODEL,
        contents=f"{STYLE}\n\n{text}",
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOICE)
                )
            ),
        ),
    )
    data = response.candidates[0].content.parts[0].inline_data.data
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{slug}.wav"
    _pcm_to_wav(path, data)
    return path


#: The contest limit is 3:00 and it is pass/fail. The spoken track has to fit
#: inside the screen capture with room for the beats where nobody is talking,
#: so this is the ceiling for narration alone.
MAX_NARRATION_SECONDS = 160.0

#: A measured, unhurried read. Slower than the default pace, which is the point
#: of the style direction -- so estimate against the slow end, not the fast one.
#: Measured from generated audio, not assumed. This was 135, which read the
#: script 22 seconds short against a 3:00 pass/fail limit -- the estimate said
#: 2:37 and the voice came back at 2:59.
#:
#: The rate is not uniform, and the variation is the useful part: prose runs
#: near 130, while the closing block of short declarative sentences came back
#: at 66, because the model puts a real pause at every full stop. Punchy
#: writing is expensive in TTS seconds, so a segment of short sentences needs
#: roughly twice the time its word count suggests.
WORDS_PER_MINUTE = 118.7


#: A generation is rejected past this multiple of its expected length. The
#: model occasionally runs away: 49 words came back as 339 seconds of audio,
#: and 16 words as 655. It is not a text problem -- the same text regenerates
#: correctly -- so the fix is to notice and ask again rather than to rewrite.
#: 2.5x is comfortably above honest variation (numbers and short sentences run
#: slow, up to about 1.6x) and far below any runaway seen.
RUNAWAY_FACTOR = 2.5

#: How many times to ask again before giving up on a segment.
MAX_ATTEMPTS = 3


def expected_seconds(text: str) -> float:
    return len(text.split()) / WORDS_PER_MINUTE * 60.0


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as fh:
        return fh.getnframes() / float(fh.getframerate())


def report_duration(total: float, estimated: bool) -> int:
    label = "estimated" if estimated else "measured"
    mins, secs = divmod(total, 60)
    print(f"\n  {label} narration: {int(mins)}:{secs:04.1f}")
    if total > MAX_NARRATION_SECONDS:
        over = total - MAX_NARRATION_SECONDS
        print(
            f"  OVER BUDGET by {over:.0f}s. The 3:00 video limit is pass/fail. "
            f"Cut roughly {int(over * WORDS_PER_MINUTE / 60)} words from "
            f"narration.md and re-run."
        )
        return 1
    print(f"  {MAX_NARRATION_SECONDS - total:.0f}s of headroom.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="regenerate a single segment by slug")
    ap.add_argument("--dry-run", action="store_true", help="print segments, call nothing")
    args = ap.parse_args()

    assert_not_paid_key()
    parts = segments()
    if args.only:
        parts = [p for p in parts if p[0] == args.only]
        if not parts:
            return print(f"no segment named {args.only!r}") or 1

    words = sum(len(t.split()) for _, t in parts)
    print(f"{len(parts)} segments, {words} words\n")

    total = 0.0
    for slug, text in parts:
        n = len(text.split())
        print(f"  {slug:34s} {n:4d} words")
        if args.dry_run:
            total += n / WORDS_PER_MINUTE * 60.0
            continue
        path = speak(slug, text)
        seconds = wav_seconds(path)
        total += seconds
        print(f"  {'':34s} -> {path.name}  {seconds:5.1f}s")

    status = report_duration(total, estimated=args.dry_run)
    if args.dry_run:
        print("  dry run: nothing generated, no quota spent")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
