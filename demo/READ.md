# The reading script

Read this aloud while the take records. Word-for-word narration from
`narration.md`, cut into the seven blocks the picture is paced to, with the
clock time each block starts on.

**The replay is paced to this narration**, so the times matter more than the
delivery. If you fall behind, do not rush to catch up — drop a word, not a
beat. Each block has about a second of air after it before the next one.

- Target pace: **121 words per minute.** Slower than feels natural. A calm
  post-production supervisor briefing a room, not an advert.
- Total **2:51** against a **3:00** pass/fail limit.
- The only thing you press is **RUN DEMO, at 0:19** — right after block 1.

---

## 0:00 — block 1 · 18 seconds · 43 words

> A visual effects studio has twelve hundred shots and a delivery date that
> does not move.
>
> Their render farm is two hundred machines running for six weeks. When it goes
> wrong, nobody finds out until the shots are late.
>
> This is Shot Clock.

*On screen: the cold board. Countdown reading 5d.*

### >>> 0:19 — PRESS RUN DEMO, then keep reading <<<

---

## 0:19 — block 2 · 15 seconds · 31 words

> Then the farm changes. Scout notices.
>
> It is a Gemini agent, reading the same Grafana a human would, through the
> Grafana MCP server. It compares each shot against its own history.

*On screen: the crew trace wakes, PromQL calls appear.*

---

## 0:35 — block 3 · 34 seconds · 85 words

> The Gaffer proves the cause.
>
> Metrics say which shot is hurting, but not which node — that label would cost
> eight thousand series. So it goes to the logs.
>
> Texture cache collapse — not licence starvation, the pools are full, and not
> a memory leak, memory is flat.
>
> Then the trace — the only signal that sees inside one frame. Texture fetch
> ate two thirds of it, the only span marked error.
>
> It says what it ruled out. That is a diagnosis, not a guess.

*On screen: Loki logs, renderer stderr, then the trace.*

---

## 1:10 — block 4 · 32 seconds · 65 words

**This is the beat the whole video exists for. Do not rush it.**

> Then Shot Clock does something telemetry cannot.
>
> This frame rendered successfully. Exit code zero, normal duration, every
> metric healthy.

*The frame appears. **Leave a full two-second pause here.** Let it hold.*

> So Shot Clock looks at it.
>
> Gemini finds fireflies — blown-out pixels from indirect light that never
> converged, scattered across the building geometry. Ninety-five percent.
>
> The render did not fail. The image did — and every visual effects house pays
> people to catch that by eye.

---

## 1:43 — block 5 · 25 seconds · 57 words

> The Producer turns that into the numbers a studio acts on.
>
> Throughput down seventy-nine percent, delivery four hundred and four hours
> late, exposure three hundred and thirteen thousand dollars.
>
> No model calculated those — ask a language model for a cost and it invents a
> new one each time. The maths is Python, and it checks itself.

*On screen: the costing tiles resolving.*

---

## 2:09 — block 6 · 26 seconds · 49 words

> And then the crew stops reading, and acts.
>
> The First AD opens an incident, adds the diagnosis to its timeline, and
> annotates the dashboard at the minute the fault began.
>
> Whoever opens that dashboard tomorrow finds the investigation already written
> down. An agent that works, not one that talks.

*On screen: write-back rows landing amber, then the annotated Grafana page.*

---

## 2:36 — block 7 · 15 seconds · 16 words

> Shot Clock — Gemini agents on Google Cloud, wired to Grafana. Twelve hundred
> shots, one date.

*On screen: the production note, then the title.*

## 2:51 — stop recording
