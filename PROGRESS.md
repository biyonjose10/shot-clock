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
| Narration script, TTS generator with enforced 2:40 budget | done |

## Left

1. **One full crew run** to record the real journal (needs ~80 Gemini calls).
   Blocked on free quota until it resets ~12:30 PM IST.
   The war room already prefers `demo-*.jsonl` over the scripted stand-in, so
   the recording drops in with no code change.
2. Deploy to Cloud Run (`./deploy.sh`) — needs gcloud installed and the Vertex
   AI, Cloud Run, Cloud Build and Secret Manager APIs enabled.
3. Judge-cold test in a private window.
4. Generate voiceover + score, record one continuous take, upload, submit.

## Waiting on the user

- Open **Alerts & IRM** in Grafana once, so `create_incident` works and the one
  budgeted run captures it.
- Approve switching `GOOGLE_API_KEY` to `GOOGLE_API_KEY_PAID` for submission.
  `assert_not_paid_key()` blocks it until then.

## Budget

Free tier is 20 requests/day per model; one agent run is about 20. Six models
are in rotation, so roughly three agent runs a day. A full crew run is four.
