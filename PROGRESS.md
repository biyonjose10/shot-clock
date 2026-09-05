# Shot Clock — progress

Deadline: **2:00 PM PT, Tue 9 Sept 2026** (2:30 AM IST Sept 10). Submit by 6 PM IST Sept 9.

## Gate 0 — Fri 5 Sept: telemetry visible in Grafana

### Done
- Repo created, MIT licence, `.gitignore`, `.env.example`.
- `commit-msg` hook rejects assistant-attribution trailers (verified both directions).
- **Python 3.14.3 cleared for ADK.** `google-adk==2.8.0` imports clean.
  Required pinning `mcp==1.29.1` — mcp 2.x renamed `McpError` -> `MCPError`,
  which ADK imports by the old name; the failure is a *silent* ImportError that
  makes `McpToolset` simply not exist. Recorded in `requirements.txt`.
- `bin/mcp-grafana.exe` v1.3.0 downloaded (prebuilt; no Go or Docker needed).
- **72 tools enumerated from the running server** -> `docs/mcp-tools.txt`.
  All headline write tools confirmed present.

### Blocked on manual setup
- Grafana Cloud account, OTLP credentials, Admin service-account token.
- Google Cloud project + billing (lead time), gcloud CLI install.

### Next
- `sim/telemetry.py` once OTLP credentials land.
- First commit, push to public GitHub repo.

## Findings that changed the plan
- `find_slow_requests` searches **Tempo** datasources via Sift, so traces are
  partially reachable through the required MCP server after all.
- `grafana_api_request` ("similar to `gh api`") is a general authenticated
  escape hatch to any Grafana API — a fallback for anything without a tool.
- `get_panel_image` returns a panel PNG as base64, which removes the Grafana
  Cloud iframe/anonymous-access problem entirely for the war room UI.
