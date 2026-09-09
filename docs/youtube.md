# YouTube title and description

Paste as-is. Public or unlisted — private fails the submission check.

## Title

    Gemini agents investigate a VFX render farm and write the incident back into Grafana

Alternates, if you want the product name to lead:

    Shot Clock — Gemini agents that find the shots about to miss the delivery date
    Shot Clock — AI agents that read Grafana, inspect the frame, and price the delay

## Description

    A visual effects studio has 1,200 shots and a delivery date that does not
    move. Shot Clock is a crew of Gemini agents that watches the render farm's
    telemetry in Grafana, works out which shots miss the date, proves why,
    inspects the rendered frame for defects telemetry cannot see, prices the
    delay, and writes the whole investigation back into Grafana.

    Built for Agentic Cinema: The Blockbuster Hackathon — Grafana Labs track.

    Try it:    https://shot-clock-669554430519.us-central1.run.app
    Code:      https://github.com/biyonjose10/shot-clock  (MIT)
    Dashboard: https://robustspring2217.grafana.net/dashboard/snapshot/p5C6mOBjNlC67o8BAi7aiVfXB5tKLRWK
               (the farm the crew investigated — opens without a Grafana account)

    THE CREW
    Scout sweeps 1,200 shots and finds the ones projected past the date.
    Gaffer proves the cause across metrics, then logs, then traces — and says
      what it ruled out, which is what makes it a diagnosis rather than a guess.
    The tech check is the part telemetry cannot do: the frame rendered
      successfully, exit code zero, every metric healthy — and the image is
      still wrong. Gemini looks at the plate and finds fireflies.
    Producer prices the delay. No model computes a number: the maths is Python
      and it reconciles its components against its total.
    First AD opens an incident, adds the diagnosis to its timeline, and
      annotates the dashboard at the minute the fault began.

    CHAPTERS
    0:00  1,200 shots and a date that does not move
    0:19  Scout sweeps the farm through the Grafana MCP server
    0:35  Gaffer: metrics, then logs, then the trace
    1:10  The tech check — a frame that passed every metric and is still wrong
    1:43  Producer prices it: 79% throughput lost, 404 hours late, $313,062
    2:09  First AD writes the investigation back into Grafana
    2:36  1,200 shots, one date

    BUILT WITH
    Gemini on Vertex AI, Google Agent Development Kit, Cloud Run, and Grafana
    Cloud — Prometheus, Loki and Tempo, read and written through the Grafana
    MCP server. The 200-node farm is simulated; the telemetry it emits, the
    agents' queries and the write-back are all real.

    The run in this video is a replay of a recorded crew run, paced to the
    narration. The journal it replays is in the repo.

## Notes

- The narration is Gemini TTS. That is deliberate and stated in
  `demo/narration.md`: an ungenerated voice would have been the only hand-made
  thing in the film.
- Attach `demo/shot-clock.srt` if you upload the clean cut rather than the
  burnt-in one. English narration already satisfies the language rule; captions
  are for the judges who watch muted.
