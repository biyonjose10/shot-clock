# Recording the take

One continuous capture, 1920×1080. Total **2:42**, against a 3:00 pass/fail limit.

## Before you press record

1. **Restart the web server.** Its farm advances ten production minutes every
   five real seconds, so a server left running an hour is weeks past the
   delivery date and the countdown reads nonsense. Fresh start puts the
   production clock at 25 Sep 05:59.

       .venv\Scripts\python.exe -m uvicorn web.server:app --port 8000

2. **Chrome page zoom to 100%** — Ctrl+0. It is per-origin and sticky, and was
   found sitting at 75%.
3. Hide the bookmarks bar, silence notifications, full-screen the browser.

## The take

Start recording on the cold board. Play the opening narration over it, then
press **RUN DEMO at 0:20**. Everything after that is automatic — the replay is
paced to the voiceover, so no further input is needed.

| time | beat | voice | audio file |
|---|---|---|---|
| 0:00 | cold board, shot grid filling | 17.9s | `0-00-0-15-the-problem-and-the-name.wav` |
| **0:20** | **press RUN DEMO** | | |
| 0:20 | Scout sweeps the farm | 15.2s | `0-35-0-55-scout.wav` |
| 0:37 | Gaffer proves the cause | 24.2s | `0-55-1-20-gaffer.wav` |
| 1:03 | tech check begins | 32.0s | `1-20-1-50-the-tech-check-the-moment.wav` |
| 1:22 | **frame + verdict, holds 14s** | | |
| 1:37 | Producer prices the delay | 24.1s | `1-50-2-15-producer.wav` |
| 2:03 | First AD writes back | 26.4s | `2-15-2-40-first-ad-writes-back.wav` |
| 2:31 | replay ends, production note on screen | 11.8s | `2-40-2-52-close.wav` |
| 2:42 | end | | |

Every slot has 1.5–2s of slack against its voiceover, so small drift is safe.

The audio filenames still carry the timings from the first draft of the script.
They are slugs, not timings — the table above is authoritative.

## After

Upload as **public or unlisted**. Private fails the submission check.

## Why the replay is paced

A real investigation does not pace itself for an audience: this run spent 66 of
its 154 seconds in the Scout and reached the tech check with 13 seconds left,
putting the frame on screen for one of them. `agent/journal.py:direct()`
re-paces each section onto the narration's beats and holds still after the
verdict. It adds, removes, reorders and edits nothing — asserted in the code —
so every number on screen is the one the agent actually read.
