"""Cut the recorded take: lay the narration on it and trim to length.

The capture is a plain screen recording with no sound. The narration is a
single track whose blocks sit at the shot list's offsets, so the only thing
that has to line up is the moment RUN DEMO was pressed: block 2 opens on it.

    python -m demo.cut --press 19.41
    python -m demo.cut --press 19.41 --check   # report, write nothing

`--press` is when the button was pressed in the recording, in seconds. The
narration is delayed by the difference between that and the shot list's 0:19
cue, so a press that landed slightly late does not drag the picture out of
sync with the voice.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FFDIR = Path(
    r"C:\Users\biyon\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\ffmpeg-9.0.1-full_build\bin"
)
FFMPEG = str(FFDIR / "ffmpeg.exe")
FFPROBE = str(FFDIR / "ffprobe.exe")

TRACK = ROOT / "demo" / "audio" / "narration-track.wav"
CUE = 19.0        # where the shot list puts the RUN DEMO press
NARRATION = 171.3  # the track's last word, from build_track
TAIL = 1.8         # a beat of picture after the final line
LIMIT = 180.0      # the pass/fail rule


def probe(path: Path) -> dict:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-print_format", "json",
         "-show_entries", "format=duration:stream=width,height,codec_type,codec_name",
         str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--take", default=None, help="the raw capture")
    ap.add_argument("--out", default=None, help="where to write the cut")
    ap.add_argument("--press", type=float, required=True)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    take = Path(args.take) if args.take else ROOT / "demo" / "take.mp4"
    out = Path(args.out) if args.out else ROOT / "demo" / "shot-clock.mp4"

    if not take.exists():
        raise SystemExit(f"no capture at {take}")
    if not TRACK.exists():
        raise SystemExit(f"no narration track at {TRACK}; run demo.build_track")

    info = probe(take)
    src = float(info["format"]["duration"])
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    print(f"capture   {src:.1f}s  {video['width']}x{video['height']}")

    # A press that landed late pushes the narration back by the same amount, so
    # the voice keeps its relationship to the picture rather than to the file.
    delay = max(0.0, args.press - CUE)
    end = delay + NARRATION + TAIL
    print(f"press at  {args.press:.2f}s (cue {CUE:.0f}s) -> delay narration {delay:.2f}s")
    print(f"final     {int(end)//60}:{end % 60:04.1f}  against a {int(LIMIT)//60}:{LIMIT % 60:04.1f} limit")

    if end > LIMIT:
        raise SystemExit("over the limit")
    if end > src:
        raise SystemExit(f"capture is only {src:.1f}s, needs {end:.1f}s")
    if args.check:
        return

    cmd = [
        FFMPEG, "-y", "-v", "error",
        "-i", str(take),
        "-itsoffset", f"{delay:.3f}", "-i", str(TRACK),
        "-map", "0:v:0", "-map", "1:a:0",
        "-t", f"{end:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        str(out),
    ]
    subprocess.run(cmd, check=True)

    got = probe(out)
    dur = float(got["format"]["duration"])
    kinds = {s["codec_type"]: s.get("codec_name") for s in got["streams"]}
    print(f"\nwrote {out}")
    print(f"  {int(dur)//60}:{dur % 60:04.1f}  {kinds}")
    if "audio" not in kinds:
        raise SystemExit("no audio stream in the output")
    if dur > LIMIT:
        raise SystemExit("output is over the limit")


if __name__ == "__main__":
    sys.exit(main())
