"""Burn the subtitles into the cut.

Judges watch a lot of these muted, and an attached subtitle file only helps if
the platform picks it up. Burnt-in captions are the version that always plays,
and the rules count them as the English-language requirement either way.

    python -m demo.burn --press 19.41

The srt is written against the narration's own clock, so it is shifted by the
same amount `demo.cut` delayed the audio: whatever the RUN DEMO press missed
the 0:19 cue by.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FFDIR = Path(
    r"C:\Users\biyon\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\ffmpeg-9.0.1-full_build\bin"
)
FFMPEG = str(FFDIR / "ffmpeg.exe")
FFPROBE = str(FFDIR / "ffprobe.exe")

CUE = 19.0
STAMP = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})")

# White with a hard black edge. No box: the UI underneath is already dark, and
# a box would sit on the backlog panel like a bruise for three minutes.
# FontSize is in the reference resolution's units, so `original_size` below is
# not optional: without it libass assumes a 288-line canvas and scales every
# size by 3.75, which puts the first caption across a third of the screen.
STYLE = (
    "FontName=Segoe UI,FontSize=20,Bold=1,"
    "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H00000000,"
    "BorderStyle=1,Outline=1.6,Shadow=1,Alignment=2,MarginV=34"
)


def shift(text: str, by: float) -> str:
    def move(m: re.Match) -> str:
        h, mi, s, ms = (int(g) for g in m.groups())
        t = max(0.0, h * 3600 + mi * 60 + s + ms / 1000 + by)
        h2, rem = divmod(t, 3600)
        m2, s2 = divmod(rem, 60)
        return f"{int(h2):02d}:{int(m2):02d}:{int(s2):02d},{int(round((s2 % 1) * 1000)):03d}"

    return STAMP.sub(move, text)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--press", type=float, required=True)
    ap.add_argument("--src", default="demo/shot-clock.mp4")
    ap.add_argument("--out", default="demo/shot-clock-subtitled.mp4")
    args = ap.parse_args()

    delay = max(0.0, args.press - CUE)
    srt = ROOT / "demo" / "shot-clock.srt"
    shifted = ROOT / "demo" / "_burn.srt"
    shifted.write_text(shift(srt.read_text(encoding="utf-8"), delay), encoding="utf-8")
    print(f"subtitles shifted by {delay:+.2f}s -> {shifted.name}")

    subprocess.run(
        [
            FFMPEG, "-y", "-v", "error",
            "-i", args.src,
            "-vf", f"subtitles=demo/_burn.srt:original_size=1920x1080:force_style='{STYLE}'",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-c:a", "copy",
            args.out,
        ],
        cwd=ROOT, check=True,
    )
    shifted.unlink(missing_ok=True)

    info = json.loads(subprocess.run(
        [FFPROBE, "-v", "error", "-print_format", "json",
         "-show_entries", "format=duration:stream=codec_type,codec_name,width,height",
         args.out],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout)
    dur = float(info["format"]["duration"])
    kinds = {s["codec_type"]: s.get("codec_name") for s in info["streams"]}
    print(f"wrote {args.out}  {int(dur)//60}:{dur % 60:04.1f}  {kinds}")
    if "audio" not in kinds:
        raise SystemExit("lost the audio stream")
    if dur > 180:
        raise SystemExit("over the limit")


if __name__ == "__main__":
    main()
