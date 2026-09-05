"""Connect the crew to Grafana through the official mcp-grafana server.

Transport choice
    The hosted Grafana Cloud MCP endpoint authenticates with OAuth 2.1, which
    needs a human to click through a browser. That cannot work from a headless
    Cloud Run container, so we run the official ``grafana/mcp-grafana`` binary
    ourselves over stdio with a service account token. The track rules allow
    either; only this one survives deployment.

Tool filtering
    The server exposes 72 tools (see ``docs/mcp-tools.txt``, generated from the
    running binary). Handing all 72 to every agent bloats the context and the
    model picks wrong, so each crew member gets only the tools its role needs.
    Every name below was taken from the live server, not from documentation.

Datasource pinning
    A Grafana Cloud stack ships three Loki datasources and two Prometheus ones.
    The alphabetically-first Loki is ``alert-state-history``, which is always
    empty -- an agent that lands there reports "no logs" and looks broken.
    The uids are pinned in the prompts and passed explicitly.
"""
from __future__ import annotations

import os
import platform
from pathlib import Path

from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]

#: Pinned datasource uids. See the module docstring for why this matters.
PROMETHEUS_UID = "grafanacloud-prom"
LOKI_UID = "grafanacloud-logs"
TEMPO_UID = "grafanacloud-traces"

#: The Go server takes a moment to start and to discover datasources. ADK's
#: default stdio timeout is far too short for that and fails opaquely.
STARTUP_TIMEOUT_SECONDS = 60

# --- per-crew tool allowlists ----------------------------------------------
# Names verified against mcp-grafana v1.3.0 running over stdio.

SCOUT_TOOLS = [
    "list_datasources",
    "list_prometheus_metric_names",
    "list_prometheus_label_names",
    "list_prometheus_label_values",
    "query_prometheus",
]

GAFFER_TOOLS = [
    "query_prometheus",
    "query_loki_logs",
    "query_loki_patterns",
    "list_loki_label_names",
    "list_loki_label_values",
    "find_error_pattern_logs",
    # Searches Tempo for slow requests -- the one route from the required MCP
    # server to trace data, since there is no TraceQL tool.
    "find_slow_requests",
    "get_sift_investigation",
]

PRODUCER_TOOLS = [
    "query_prometheus",
    "query_prometheus_histogram",
    "list_prometheus_metric_names",
]

# First AD is the only crew member with write access, and the reason the
# project is not just another read-only dashboard chatbot.
FIRST_AD_TOOLS = [
    "create_incident",
    "add_activity_to_incident",
    "update_incident",
    "list_incidents",
    "create_annotation",
    "update_dashboard",
    "create_snapshot",
    "generate_deeplink",
    "get_panel_image",
    "search_dashboards",
]


def binary_path() -> str:
    """The vendored mcp-grafana binary, or whatever is on PATH."""
    name = "mcp-grafana.exe" if platform.system() == "Windows" else "mcp-grafana"
    local = ROOT / "bin" / name
    return str(local) if local.exists() else "mcp-grafana"


def _server_env() -> dict[str, str]:
    url = os.environ.get("GRAFANA_URL", "").strip()
    token = os.environ.get("GRAFANA_SERVICE_ACCOUNT_TOKEN", "").strip()
    if not url or not token:
        raise RuntimeError(
            "GRAFANA_URL and GRAFANA_SERVICE_ACCOUNT_TOKEN must be set; "
            "copy .env.example to .env and fill them in"
        )
    return {
        **os.environ,
        "GRAFANA_URL": url,
        "GRAFANA_SERVICE_ACCOUNT_TOKEN": token,
    }


def toolset(tool_filter: list[str]) -> McpToolset:
    """Build an McpToolset exposing only ``tool_filter``.

    Note there is deliberately no ``--disable-write``: the write tools are the
    entire point of First AD.
    """
    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command=binary_path(),
                args=["--transport", "stdio"],
                env=_server_env(),
            ),
            timeout=STARTUP_TIMEOUT_SECONDS,
        ),
        tool_filter=tool_filter,
    )


#: Shared context every crew prompt gets, so no agent has to guess a uid.
DATASOURCE_BRIEFING = f"""
Grafana datasource uids -- always pass these explicitly, never guess:
  Prometheus (metrics): {PROMETHEUS_UID}
  Loki (logs):          {LOKI_UID}
  Tempo (traces):       {TEMPO_UID}

Do not use any other Loki datasource. The stack also exposes
'grafanacloud-alert-state-history' and 'grafanacloud-usage-insights', which
contain no render farm data and will look like an outage if you query them.

The render farm emits, in Prometheus:
  node_memory_bytes{{node,health}}                 per render node
  shot_frames_completed_total{{shot_id,...}}       per shot
  render_frame_duration_seconds{{shot_id,...}}     per shot, mean seconds/frame
  render_job_status{{shot_id,status,at_risk,...}}  1 while rendering
  licence_pool_available                          farm-wide
  texture_cache_hit_ratio                         farm-wide, 0-1
  queue_depth                                     farm-wide

Shot-scoped metrics carry no 'node' label, on purpose: crossing node with
shot_id would blow the series budget. To tie a shot to a node, read the logs
in Loki ({{service_name="render-farm"}}) or the traces in Tempo, which both
carry shot and node together.
""".strip()
