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
4. **Vertex AI and AI Studio expose disjoint model sets.** On the free AI
   Studio project `gemini-2.5-flash` is refused as "no longer available to new
   users" while the 3.x line works; on Vertex it is exactly reversed and every
   3.x id returns "Publisher model ... was not found". `agent/models.py`
   resolves the model from `GOOGLE_GENAI_USE_VERTEXAI`. A build tested only
   against AI Studio 404s for every visitor once deployed.
5. **The stack has three Loki datasources.** The first alphabetically is
   `alert-state-history`, permanently empty. Uids are pinned in `agent/mcp.py`.
6. **Grafana Cloud has no anonymous access**, so dashboards cannot be iframed
   directly. Snapshots are the embed path, and `create_snapshot` needs a real
   dashboard payload — an empty one returns "Dashboard not found".
7. **The incident plugin is `grafana-irm-app`, not `grafana-incident-app`.**
   Grafana merged Incident and OnCall into IRM and the old id 404s with
   "Plugin not found". Incidents also need the org's counter row to exist:
   the first CreateIncident call creates it, and until then the MCP tool fails
   with a foreign-key constraint on `grafana_incident.Counters`. Both are now
   done on this stack and `create_incident` works over MCP.

8. **The farm runs on PRODUCTION time, not the wall clock.** It starts six
   production days before the delivery date and advances ten production
   minutes every five real seconds. Anything measuring the delivery window
   with `datetime.now()` reads a different clock: on 6 September that returned
   587 hours, enough that a farm crippled to a third of its throughput still
   "delivered early" and priced at zero exposure. The farm publishes
   `farm_seconds_to_delivery`; read that.

9. **The war room draws its report panel from COSTING and WRITE_BACK events**,
   not from tool calls. Only the scripted stand-in emitted them at first, so
   real runs produced journals the UI could not fully draw. `agent/runtime.py`
   now emits both from the tool callbacks.

10. **A turn cap that binds in normal operation severs the agent's answer.** An
    agent only writes its summary once it stops calling tools, and a tool call
    costs two journal events, so a cap set near 2x the expected query count
    fires the turn before the summary. `MAX_TURNS_PER_AGENT` is a runaway
    catcher, not a working limit.

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
