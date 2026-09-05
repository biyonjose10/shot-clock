# Shot Clock — progress

Deadline **2026-09-09, 2:00 PM PT**. Submitting by 6 PM IST on the 9th.
Public repo, MIT: https://github.com/biyonjose10/shot-clock

## Done

| | |
|---|---|
| Gate 0 — metrics, logs AND traces live in Grafana Cloud | done |
| Gate 1 — Scout diagnosing over real MCP calls | done |
| Gaffer — separated licence starvation from cache collapse | done |
| Producer — 3 tool calls, deterministic self-reading costing | done |
| First AD — real annotation + deeplink written to Grafana | done |
| Gemini vision tech check — 4/4 on clean and three defects | done |
| War room console + DEMO MODE replay | done |
| Write-back proven: annotation, dashboard, snapshot | done |
| README, Dockerfile, deploy.sh, ADK root_agent | done |
| **Deployed to Cloud Run**, judge-cold tested | done |
| Narration script, TTS generator with enforced 2:40 budget | done |

## Left

1. **One full crew run** to record the real journal (needs ~80 Gemini calls).
   Blocked on free quota until it resets ~12:30 PM IST.
   The war room already prefers `demo-*.jsonl` over the scripted stand-in, so
   the recording drops in with no code change.
2. Wire `agent/live_check.py` into the web server as the one live Vertex AI
   call, so the runtime requirement is satisfied. Built and capped; not yet
   exposed as an endpoint. Goes in with the real journal so we deploy once.
3. Header truncates the film title below ~1600px wide. Cosmetic, but judges
   use varied screens.
4. Generate voiceover + score, record one continuous take, upload, submit.

## Waiting on the user

- Open **Alerts & IRM** in Grafana once, so `create_incident` works and the one
  budgeted run captures it.
- Approve switching `GOOGLE_API_KEY` to `GOOGLE_API_KEY_PAID` for submission.
  `assert_not_paid_key()` blocks it until then.

## Budget

Free tier is 20 requests/day per model; one agent run is about 20. Six models
are in rotation, so roughly three agent runs a day. A full crew run is four.

## Deployment

Live: https://shot-clock-669554430519.us-central1.run.app
Cold start measured at 0.72s, which is why min-instances stays at 0.

    ./gcloud.sh ...            locally installed CLI, nothing on system PATH
    ./setup_gcp.sh             APIs, secrets, IAM (one time, done)
    ./deploy.sh                build and deploy

A $3 budget alert is armed at 50/90/100%. The deployed app imports only the
journal module, so no visitor can make it spend Gemini credit; the one live
call is added deliberately and capped at 40 a day.
