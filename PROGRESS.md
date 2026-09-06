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
| Write-back proven: annotation, dashboard, snapshot, incident | done |
| README, Dockerfile, deploy.sh, ADK root_agent | done |
| **Deployed to Cloud Run**, judge-cold tested | done |
| Narration script, TTS generator with enforced 2:40 budget | done |
| Crew runs resumable across days after a 429 | done |
| Costing reads the production clock, not the wall clock | done |

## Left

1. **One full crew run** to record the real journal. The war room prefers a
   complete `demo-*.jsonl`, so the recording drops in with no code change.
2. **Reconcile the narration with that run.** The script quotes 58% throughput
   loss, 31 hours past delivery and $143,000 exposure; those came from an
   earlier build and will move. The voiceover has **0s of headroom** at 2:40,
   so corrections must not add words.
3. Redeploy with the real journal.
4. Generate voiceover + score, record one continuous take, upload, submit.

## Recording notes

- **Restart the web server immediately before filming.** Its farm advances ten
  production minutes every five real seconds, so a server left up for an hour
  is weeks past the delivery date and the countdown reads nonsense.
- **Chrome page zoom must be 100%** (Ctrl+0). It is per-origin and sticky.
- 1920×1080, no bookmarks bar, no notifications, one continuous capture.

## Budget

Free tier is 20 requests/day per model; one crew member is about one model's
whole daily budget. Six models are in rotation. A failed run no longer costs a
day: stages checkpoint as they finish and `--resume` continues on fresh quota.

    python -m agent.orchestrator                    # full crew
    python -m agent.orchestrator --resume RUN_ID    # continue after a 429

## Deployment

Live: https://shot-clock-669554430519.us-central1.run.app
Cold start measured at 0.72s, which is why min-instances stays at 0.

    ./gcloud.sh ...            locally installed CLI, nothing on system PATH
    ./setup_gcp.sh             APIs, secrets, IAM (one time, done)
    ./deploy.sh                build and deploy

Verified on the deployed service: `GOOGLE_GENAI_USE_VERTEXAI=TRUE` is set and
no `GOOGLE_API_KEY` is present, so it cannot fall back to the free key. A $3
budget alert is armed at 50/90/100%. Artifact Registry holds one image.
