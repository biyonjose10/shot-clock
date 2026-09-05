# Shot Clock — progress

Deadline: **2:00 PM PT, Tue 9 Sept 2026** (2:30 AM IST Sept 10). Submit by 6 PM IST Sept 9.

## GATE 0 — PASSED (Fri 5 Sept)

All three signals confirmed landing in Grafana Cloud stack `robustspring2217`:

| Signal | Evidence |
|---|---|
| Metrics | 201 node series, 48 per shot metric, 1 each farm-wide (~350 active, 10k budget) |
| Logs | LogQL returns Arnold/Karma stderr with shot_id, node, artist |
| Traces | Tempo returns `render_frame` traces, per-stage sub-spans, 330s under fault |

- Repo + MIT licence, 10 clean commits, `commit-msg` hook blocks assistant trailers.
- `google-adk==2.8.0` works on Python 3.14.3 — required pinning `mcp==1.29.1`
  (mcp 2.x renamed `McpError`->`MCPError`; ADK's import fails *silently* and
  `McpToolset` simply ceases to exist).
- 72 MCP tools enumerated from the running binary -> `docs/mcp-tools.txt`.
  All 26 allowlisted names validated against it.
- Simulator: 1200 shots, 200 nodes, licence pool, texture cache, 4 faults,
  runs fully offline with `--dry-run`.
- Journal record/replay spine built and self-tested.

### Traps found and neutralised
1. **`mcp<2` pin** — see above. Silent failure, would have cost a day.
2. **Three Loki datasources.** The alphabetically-first is
   `alert-state-history`, permanently empty. An agent landing there reports
   "no logs" and looks broken. Uids pinned in `agent/mcp.py`.
3. **Defect frames were a different image** from the clean frame — the defect
   was mixed into the composition seed. Clean/corrupt now share a base plate.
4. **Fireflies read as a starfield** at the original density; a vision model
   would call that a creative choice, not a defect. Reduced to sparse isolated
   hot pixels that fall on buildings and road where stars cannot.

## Blocked on manual setup
- **Google Cloud project + billing + Gemini API key** — now the critical path.
  Gate 1 cannot start without a key.
- **Push to public GitHub** — awaiting go-ahead.

## Next (Gate 1, Sat 6 Sept)
- Scout answering "which shots are at risk and why" over real MCP calls.
- Gemini vision tech-check on a corrupt frame (pulled forward from Gate 2).
- War room UI shell (in progress).

## Known tuning, not blocking
- Baseline node memory already peaks near 100%, so Scout must detect OOM by
  rate of climb, not absolute value.
- 900+ of 1200 shots read as at-risk once a farm-wide fault lands. Producer's
  dollar figure is more legible with a handful. Tune at Gate 2.
