"""Lay the seven voiceover segments onto one continuous narration track.

The take is a single unedited screen capture, so the narration has to play
while it is being captured. Seven files cannot be triggered by hand on cue in
the middle of a take that also needs RUN DEMO pressed at nineteen seconds, so
this writes them into one wav at the offsets `RECORDING.md` gives, with silence
in between. Press play on this, start recording, and the only thing left to do
by hand is the button.

    python -m demo.build_track            # write demo/audio/narration-track.wav
    python -m demo.build_track --check    # report the layout, write nothing
"""
from __future__ import annotations

import argparse
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIO = ROOT / "demo" / "audio"
OUT = AUDIO / "narration-track.wav"

# (start second, filename). The times are the shot list's, not the filenames'
# — the slugs still carry timings from the first draft of the script.
LAYOUT = [
    (0, "0-00-0-15-the-problem-and-the-name.wav"),
    (19, "0-35-0-55-scout.wav"),
    (35, "0-55-1-20-gaffer.wav"),
    (70, "1-20-1-50-the-tech-check-the-moment.wav"),
    (103, "1-50-2-15-producer.wav"),
    (129, "2-15-2-40-first-ad-writes-back.wav"),
    (156, "2-40-2-52-close.wav"),
]

LIMIT = 180  # the hard pass/fail rule, in seconds


def clock(seconds: float) -> str:
    return f"{int(seconds) // 60}:{seconds % 60:04.1f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report, write nothing")
    args = ap.parse_args()

    segments = []
    params = None
    for start, name in LAYOUT:
        path = AUDIO / name
        if not path.exists():
            raise SystemExit(f"missing segment: {path}")
        with wave.open(str(path), "rb") as w:
            if params is None:
                params = w.getparams()
            elif (w.getnchannels(), w.getframerate(), w.getsampwidth()) != (
                params.nchannels,
                params.framerate,
                params.sampwidth,
            ):
                raise SystemExit(f"{name} does not match the other segments")
            segments.append((start, name, w.readframes(w.getnframes())))

    assert params is not None
    rate, width, channels = params.framerate, params.sampwidth, params.nchannels
    frame = width * channels

    # Check the layout before writing it: a segment that runs past the next cue
    # would be talking over the following beat, which is worse than silence.
    end = 0.0
    for i, (start, name, data) in enumerate(segments):
        length = len(data) / frame / rate
        finish = start + length
        nxt = segments[i + 1][0] if i + 1 < len(segments) else None
        flag = ""
        if nxt is not None and finish > nxt:
            flag = f"  <-- OVERRUNS the {clock(nxt)} cue by {finish - nxt:.1f}s"
        print(f"{clock(start):>6}  {length:5.1f}s  ends {clock(finish):>6}  {name}{flag}")
        end = max(end, finish)

    print(f"\ntotal {clock(end)} against a {clock(LIMIT)} limit")
    if end > LIMIT:
        raise SystemExit("the track is over the limit")

    if args.check:
        return

    total_frames = int(end * rate) + rate  # a second of air at the tail
    silence = b"\x00" * frame
    buf = bytearray(silence * total_frames)
    for start, _, data in segments:
        at = int(start * rate) * frame
        buf[at:at + len(data)] = data

    with wave.open(str(OUT), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(bytes(buf))
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
