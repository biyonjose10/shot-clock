# Shot Clock — 3-minute trailer

Word-for-word narration, timed to DEMO MODE. Generated with Gemini TTS; the
score is Lyria. Nothing is recorded by hand and nothing is edited — the run is
one continuous screen capture, and the captions are rendered by the UI itself.

**Total: 2:52.** Eight seconds of headroom against the 3:00 limit.

Voice direction: a calm, unhurried post-production supervisor. Not an advert.
Let the picture carry the excitement.

---

## 0:00 – 0:15 · The problem, and the name

> A visual effects studio has one thousand two hundred shots, and a delivery
> date that does not move.
>
> Their render farm is two hundred machines that will run for six weeks. When
> it goes wrong, nobody finds out until the shots are late.
>
> This is Shot Clock.

*On screen: the war room, cold. Shot board filling. Delivery countdown running.*

**Why these words:** the first fifteen seconds are where a judge decides whether
to keep watching, so they state the stakes and the product name and nothing
else. No architecture, no technology names.

---

## 0:15 – 0:35 · The farm is real

> Every shot on this board is being rendered right now, by a simulated farm
> that streams real metrics, real logs and real traces into Grafana Cloud.
>
> Frame times. Node memory. Licence pools. Texture cache. One trace per frame,
> with a span for every stage of the render.

*On screen: shot cards updating, farm vitals ticking. Cut to the embedded
Grafana panel showing live series.*

---

## 0:35 – 0:55 · Scout

> At four minutes past midnight, the farm changes.
>
> Scout notices. It is a Gemini agent, and it is reading the same Grafana that
> a human would — through the Grafana MCP server.
>
> It does not compare against a threshold. It compares each shot against its
> own recent history, and finds the ones that got slower.

*On screen: the crew trace panel. `query_prometheus` calls appearing with their
real PromQL, responses coming back.*

---

## 0:55 – 1:20 · Gaffer

> The Gaffer proves the cause.
>
> Metrics say which shot is hurting. They deliberately do not say which node —
> that label would cost eight thousand time series. So the Gaffer does what an
> engineer does: it goes to the logs.

*On screen: `query_loki_logs`, then the returned renderer stderr.*

> Texture cache collapse. Not licence starvation — the pools are full. Not a
> memory leak — node memory is flat.
>
> It says what it ruled out, and why. That is the difference between a
> diagnosis and a guess.

---

## 1:20 – 1:50 · The tech check — the moment

> Then Shot Clock does something telemetry cannot do.
>
> This frame rendered successfully. Exit code zero. Normal duration. Every
> metric on this shot is healthy.

*On screen: the frame appears. Beat. Hold on it.*

> So Shot Clock looks at it.
>
> Gemini inspects the actual plate, and finds fireflies — blown-out pixels from
> indirect light that never converged. Scattered across the geometry, and across
> the road, where stars could not be.
>
> The render did not fail. The image did. No metric in the world would have
> caught that, and every visual effects house on earth pays people to catch it
> by eye.

**This is the beat the whole video exists for. Do not rush it. Let the frame
hold for a full two seconds before the verdict lands.**

---

## 1:50 – 2:15 · Producer

> The Producer turns it into the only two numbers a studio acts on: the date,
> and the money.

*On screen: the costing tiles resolving.*

> Throughput down fifty-eight percent. Thirty-one hours past delivery.
> A hundred and forty-three thousand dollars of exposure.
>
> No model calculated those. A language model asked to estimate a cost will
> invent a confident, different number every time. The maths is Python, it
> reads the farm itself, and it checks that its own figures add up.

---

## 2:15 – 2:40 · First AD writes back

> And then the crew stops reading, and acts.
>
> The First AD opens an incident. Adds the diagnosis to its timeline. Stamps an
> annotation on the dashboard at the exact minute the fault began. Creates a
> snapshot, and a link a human can click.

*On screen: the write-back rows landing amber, then the real Grafana page with
the annotation on it.*

> Tomorrow morning, whoever opens that dashboard finds the investigation
> already written down. That is the difference between an agent that talks and
> an agent that works.

---

## 2:40 – 2:52 · Close

> Shot Clock. Built on Gemini and the Agent Development Kit, running on Google
> Cloud, wired to Grafana through the Model Context Protocol.
>
> Twelve hundred shots. One date. A crew that never sleeps.

*On screen: the production note, then the title.*

---

## Recording notes

- One continuous capture. No cuts.
- 1920×1080, browser at 100% zoom, no bookmarks bar, no notifications.
- Start recording **two seconds before** pressing RUN DEMO so the cold board is
  visible.
- The UI renders its own captions, so the picture is legible with the sound off
  — which matters, because judges watch a lot of these muted.
- Upload as **public** or **unlisted**, not private. Private fails the check.
