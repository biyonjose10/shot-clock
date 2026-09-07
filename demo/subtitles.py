"""Build the subtitle track from the narration and the generated audio.

Judges watch these muted -- the project's own recording notes say so -- and the
UI's captions name the beat without carrying the argument. "No model calculated
those numbers" is the point of the Producer section and a silent viewer would
never get it.

Timings are not estimated. Each cue starts where its section starts in the
directed replay, and runs for the measured length of its own .wav. That is the
same arithmetic the pacing uses, so the subtitles cannot drift from the picture
unless the audio itself is regenerated.

    python -m demo.subtitles          # writes demo/shot-clock.srt
"""
from __future__ import annotations

import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.journal import DEMO_SECTION_SECONDS  # noqa: E402
from demo.voiceover import OUT, segments  # noqa: E402

#: Seconds of cold board before RUN DEMO is pressed. The opening narration
#: plays over it, so it has to be at least as long as that segment.
PREROLL = 19.0

#: Which section each spoken segment belongs to, by slug fragment. The audio
#: filenames still carry timings from the first draft of the script; they are
#: slugs, not timings, and the section map is what is authoritative.
SECTION_OF = {
    "scout": "scout",
    "gaffer": "gaffer",
    "tech-check": "vision",
    "producer": "producer",
    "first-ad": "first_ad",
}

#: Longest a single cue stays on screen before being split.
MAX_CUE_SECONDS = 6.0

#: Roughly the most text a viewer reads comfortably in one cue.
MAX_CUE_CHARS = 84


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as fh:
        return fh.getnframes() / float(fh.getframerate())


def stamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def section_start(slug: str) -> float | None:
    """Where this segment's audio begins in the finished video."""
    for fragment, section in SECTION_OF.items():
        if fragment in slug:
            offset = PREROLL
            for name, length in DEMO_SECTION_SECONDS.items():
                if name == section:
                    return offset
                offset += length
            return None
    return None


def split(text: str) -> list[str]:
    """Break a segment into cue-sized pieces on sentence boundaries."""
    pieces, current = [], ""
    for part in text.replace(" — ", " — |").split("|"):
        for sentence in part.replace(". ", ".|").split("|"):
            sentence = sentence.strip()
            if not sentence:
                continue
            candidate = f"{current} {sentence}".strip()
            if current and len(candidate) > MAX_CUE_CHARS:
                pieces.append(current)
                current = sentence
            else:
                current = candidate
    if current:
        pieces.append(current)
    return pieces


def cues() -> list[tuple[float, float, str]]:
    out: list[tuple[float, float, str]] = []
    for slug, text in segments():
        audio = OUT / f"{slug}.wav"
        if not audio.exists():
            raise SystemExit(f"missing audio for {slug}; run demo.voiceover first")
        spoken = wav_seconds(audio)

        start = section_start(slug)
        if start is None:
            # The opening plays over the cold board; the close plays out after
            # the replay has finished.
            start = 0.0 if "problem" in slug else PREROLL + sum(
                DEMO_SECTION_SECONDS.values()
            )

        parts = split(text)
        # Share the segment's measured length across its cues by weight, so a
        # long sentence holds longer than a short one.
        total_chars = sum(len(p) for p in parts) or 1
        cursor = start
        for part in parts:
            length = spoken * len(part) / total_chars
            out.append((cursor, cursor + length, part))
            cursor += length
    return out


#: Written here rather than to stdout. A shell redirect on Windows encodes as
#: cp1252, which turned every em dash into a lone 0x97 byte and left the file
#: invalid UTF-8 -- a subtitle track no player is obliged to read.
SRT = ROOT / "demo" / "shot-clock.srt"


def main() -> int:
    lines = []
    for n, (start, end, text) in enumerate(cues(), 1):
        lines += [str(n), f"{stamp(start)} --> {stamp(end)}", text, ""]
    SRT.write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(lines) // 4} cues -> {SRT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
