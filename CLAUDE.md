# Shot Clock

Agentic Cinema hackathon entry, **Grafana Labs track**. Deadline **2026-09-09,
2:00 PM PT**; submitting by 6 PM IST on the 9th. Public repo, MIT, at
https://github.com/biyonjose10/shot-clock

A crew of Gemini agents watches a simulated 200-node VFX render farm's telemetry
in Grafana, works out which of 1,200 shots miss the delivery date, proves why,
inspects the rendered frame for defects telemetry cannot see, prices the delay,
and writes the investigation back into Grafana.

## Run

```
.venv\Scripts\python.exe -m sim.main --fault texture-cache-miss   # the farm
.venv\Scripts\python.exe -m agent.orchestrator                    # the crew
.venv\Scripts\python.exe -m uvicorn web.server:app --port 8000    # the war room
.venv\Scripts\python.exe -m sim.main --dry-run                    # no credentials needed
```

Checks that cost nothing and should be run before debugging anything else:

```
scripts/preflight.py         credentials, OTLP ingest, Grafana read AND write
scripts/verify_telemetry.py  metrics, logs and traces actually landed
scripts/verify_writeback.py  annotation, dashboard and snapshot writes work
scripts/list_mcp_tools.py    enumerate MCP tools from the running binary
```

## Traps already paid for — do not rediscover these

1. **`mcp` must stay pinned below 2.0.** 2.x renamed `McpError` to `MCPError`;
   `google-adk` imports the old name and swallows the ImportError, so
   `McpToolset` silently ceases to exist.
2. **`client.models.list()` lies.** It advertises models the key cannot call.
   A new project is a "new user" and is refused older models with a 404 raised
   only at call time. Test with a real request.
3. **Free-tier Gemini is 20 requests per day PER MODEL.** One agent run is ~20.
   `agent/models.py` rotates a model per crew role and keeps a usage ledger.
   `assert_not_paid_key()` refuses the paid key unless `SHOT_CLOCK_ALLOW_PAID=1`.
4. **The stack has three Loki datasources.** The first alphabetically is
   `alert-state-history`, permanently empty. Uids are pinned in `agent/mcp.py`.
5. **Grafana Cloud has no anonymous access**, so dashboards cannot be iframed
   directly. Snapshots are the embed path, and `create_snapshot` needs a real
   dashboard payload — an empty one returns "Dashboard not found".
6. **Grafana Incident is not initialised on this org.** `create_incident` fails
   on a foreign-key constraint. Annotation, dashboard and snapshot all work,
   which is why write-back was never allowed to rest on incidents alone.

## Rules that shaped the design

- **No model computes a number.** `agent/economics.py` does the maths and
  asserts its components reconcile with its total; `agent/readings.py` reads the
  farm itself so even the *inputs* are not model-chosen. An earlier version let
  the agent supply readings and it produced $172,515 and $283,720 for the same
  farm a minute apart.
- **Missing telemetry raises, never defaults.** Defaulting to zero once produced
  a 6,691-day slip and $132m of exposure from an idle farm.
- **Shot-scoped metrics carry no `node` label** — it would be 8,000 series
  against a 10k free-tier budget. The node travels on the logs instead.
- **Crew order is fixed, agents are autonomous.** A model-routed orchestrator
  would spend a round trip on a known decision and could reorder the take.

## Repo hygiene

No assistant attribution in commits, code, comments or README — a `commit-msg`
hook in `.git/hooks/` rejects it. Stage explicit paths; never `git add -A`.
