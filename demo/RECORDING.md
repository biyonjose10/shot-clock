# Recording the take

One continuous capture, 1920×1080. **Total 2:51**, against a 3:00 pass/fail limit.

## Before you press record

1. **Restart the web server.** Not optional. Its farm advances ten production
   minutes every five real seconds across a six-day window, so it reaches the
   delivery date after about fourteen minutes of uptime. A recycled farm is
   fine to look at, but a freshly started one opens at 25 Sept 05:59 with the
   full window ahead of it, which is what the countdown should read.

       .venv\Scripts\python.exe -m uvicorn web.server:app --port 8000

2. **Open the board at `http://localhost:8000/?autostart=0`.** Not the bare
   URL. The page starts the replay by itself after 900ms so that a judge who
   lands on it sees the product without hunting for a button; `autostart=0`
   holds it cold, which is what the take needs - the opening narration plays
   over a still board and you press RUN DEMO on cue.
3. **Chrome page zoom to 100%** — Ctrl+0. It is per-origin and sticky, and has
   twice been found sitting at 75%.
4. Hide the bookmarks bar, silence notifications, full-screen the browser.
5. Check the shot board has cards with content in them and the countdown reads
   about `5d`. If the board is empty or the countdown shows `+`, the server has
   been up too long — restart it.

## The take

Start recording on the cold board and let the opening narration play over it.
Press **RUN DEMO at 0:19**. Everything after that is automatic: the replay is
paced to the voiceover, so there is nothing else to touch.

| time | beat | voice | audio file |
|---|---|---|---|
| 0:00 | cold board, shot grid filling | 17.9s | `0-00-0-15-the-problem-and-the-name.wav` |
| **0:19** | **press RUN DEMO** | | |
| 0:19 | Scout sweeps the farm | 15.2s | `0-35-0-55-scout.wav` |
| 0:35 | Gaffer: metrics → logs → **trace** | 34.1s | `0-55-1-20-gaffer.wav` |
| 1:10 | tech check begins | 32.0s | `1-20-1-50-the-tech-check-the-moment.wav` |
| 1:28 | **frame + verdict, holds 14s** | | |
| 1:43 | Producer prices the delay | 25.3s | `1-50-2-15-producer.wav` |
| 2:09 | First AD writes back | 26.4s | `2-15-2-40-first-ad-writes-back.wav` |
| 2:36 | replay ends, production note on screen | 15.3s | `2-40-2-52-close.wav` |
| 2:51 | end | | |

Every slot clears its voice line by roughly a second, so small drift is safe.

The audio filenames still carry timings from the first draft of the script.
They are slugs, not timings — this table is authoritative.

## Subtitles

`demo/shot-clock.srt` is generated from these same numbers:

    python -m demo.subtitles

Upload it with the video. Judges watch muted, and the UI's captions name each
beat without carrying the argument.

## After

- Upload as **public or unlisted**. Private fails the submission check.
- Title it for the differentiator, not the project: agents that investigate a
  render farm and write the incident back into Grafana.
- Put the live URL, the repo and the public dashboard snapshot in the
  description.
- **Do not delete the Cloud Run service or make the repo private until results
  are announced.** Judging runs for weeks after the deadline and the Devpost
  entry links the live URL.

## Why the replay is paced

A real investigation does not pace itself for an audience: the recorded run
spent 66 of its 154 seconds in the Scout and reached the tech check with 13
seconds left, putting the frame on screen for one of them. `direct()` in
`agent/journal.py` stretches and compresses each section onto the narration's
beats and holds still after the verdict.

It adds, removes, reorders and alters nothing — same events, same order, same
payloads, asserted in `tests/test_invariants.py`. Only the rate of playback
changes, which the existing `speed` and `max_gap` arguments already did. The
README says so plainly too.
