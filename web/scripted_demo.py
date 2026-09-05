"""A scripted stand-in journal, so the console is demonstrable with no keys.

DEMO MODE replays a *recorded* run of the real crew. Until such a recording
exists on disk, this module writes a scripted journal in exactly the same
format, at a plausible cadence, so the UI can be built and shown today. It is
written to ``journals/`` under a name that says what it is, and ``/api/demo``
reports ``synthetic: true`` when it is what got replayed — the recording of a
genuine run must never be confused with a script.

It doubles as the written-down payload contract for the crew: every key the
front end reads appears below at least once.

    kind             actor      payload   (never a key called "kind" or
                                        "actor": Journal.record owns those)
    ---------------  ---------  ----------------------------------------------
    run_start        system     run_id, mode, scenario, film, delivery, shots
    caption          system     text, duration_ms
    agent_start      <crew>     role, goal
    agent_thought    <crew>     text
    tool_call        <crew>     tool, call_id, args, why
    tool_result      <crew>     tool, call_id, ok, latency_ms, summary, rows
    vision_verdict   gaffer     shot_id, frame, verdict, defect, confidence,
                                note, metrics_clean
    costing          producer   shots_at_risk, shot_hours, cost_usd, node_hours,
                                lines[{label, value}]
    write_back       first_ad   resource, target, title, url, id, note
    run_end          system     status, headline, note, duration_s
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agent.journal import (
    AGENT_START,
    AGENT_THOUGHT,
    CAPTION,
    COSTING,
    JOURNAL_DIR,
    RUN_END,
    RUN_START,
    TOOL_CALL,
    TOOL_RESULT,
    VISION_VERDICT,
    WRITE_BACK,
    Event,
)

ROOT = Path(__file__).resolve().parents[1]

#: Journals matching ``live-*.jsonl`` are gitignored, which is exactly right
#: for a generated file: it is a stand-in, not something to ship as evidence.
FILENAME_STEM = "live-synthetic-demo"
JOURNAL_PATH = JOURNAL_DIR / f"{FILENAME_STEM}.jsonl"

GRAFANA = os.environ.get("GRAFANA_URL", "https://shotclock.grafana.net").rstrip("/")
INCIDENT_ID = "3f21c9"
INCIDENT_URL = f"{GRAFANA}/a/grafana-incident-app/incidents/{INCIDENT_ID}"
DASHBOARD_URL = f"{GRAFANA}/d/shotclock-farm/render-farm?from=now-6h&to=now"

#: A rendered plate to show beside the vision verdict, if one has been written.
#: ``sim.frames`` writes these; on a fresh clone the directory is empty and the
#: UI simply omits the thumbnail.
FRAME_CANDIDATES = (
    "RC_0410_0112_fireflies.png",
    "OD_0270_0011_fireflies.png",
    "RC_0410_0112_black.png",
    "RC_0410_0112_missing_texture.png",
)


def _frame_url() -> str | None:
    for name in FRAME_CANDIDATES:
        if (ROOT / "web" / "static" / "frames" / name).exists():
            return f"/static/frames/{name}"
    return None


def _beats() -> list[tuple[float, str, str, dict[str, Any]]]:
    """The run, beat by beat. Offsets are seconds from the top of the run."""
    frame = _frame_url()
    return [
        (0.0, RUN_START, "system", {
            "run_id": "demo-oom-cache",
            "mode": "demo",
            "scenario": "node memory leak + texture cache collapse",
            "film": "THE LAST TRANSMISSION",
            "delivery": "2026-09-30",
            "shots": 1200,
        }),
        (0.6, CAPTION, "system", {
            "text": "1,200 shots. One delivery date. Four agents watching the farm.",
            "duration_ms": 5200,
        }),

        # --- SCOUT: who is going to miss? ---------------------------------
        (2.0, AGENT_START, "scout", {
            "role": "Scout",
            "goal": "Find every shot projected to land after the delivery date",
        }),
        (3.0, AGENT_THOUGHT, "scout", {
            "text": "Delivery is in 4 days 06:12 of production time. I need the "
                    "at-risk projection first, then whether it is clustered or "
                    "farm-wide — that decides who I hand this to.",
        }),
        (4.6, TOOL_CALL, "scout", {
            "tool": "list_prometheus_metric_names",
            "call_id": "sc-1",
            "args": {"datasourceUid": "grafanacloud-prom", "regex": "render_.*|shot_.*"},
            "why": "confirm the farm is actually reporting before trusting a query",
        }),
        (6.1, TOOL_RESULT, "scout", {
            "tool": "list_prometheus_metric_names",
            "call_id": "sc-1",
            "ok": True,
            "latency_ms": 412,
            "summary": "9 metrics: render_job_status, render_frame_duration_seconds, "
                       "shot_frames_completed_total, shots_at_risk, queue_depth, ...",
        }),
        (7.4, TOOL_CALL, "scout", {
            "tool": "query_prometheus",
            "call_id": "sc-2",
            "args": {
                "datasourceUid": "grafanacloud-prom",
                "expr": "sum(render_job_status{at_risk=\"true\"}) by (sequence)",
                "queryType": "instant",
            },
        }),
        (9.2, TOOL_RESULT, "scout", {
            "tool": "query_prometheus",
            "call_id": "sc-2",
            "ok": True,
            "latency_ms": 638,
            "summary": "34 shots at risk, concentrated in two sequences",
            "rows": [
                {"sequence": "SEQ_0500_TUNNEL_COLLAPSE", "value": 14},
                {"sequence": "SEQ_0800_FINAL_ASCENT", "value": 11},
                {"sequence": "SEQ_0300_MARKET_CROWD", "value": 6},
                {"sequence": "SEQ_0400_ROOFTOP_CHASE", "value": 3},
            ],
        }),
        (10.9, TOOL_CALL, "scout", {
            "tool": "query_prometheus",
            "call_id": "sc-3",
            "args": {
                "datasourceUid": "grafanacloud-prom",
                "expr": "avg_over_time(render_frame_duration_seconds[30m])",
                "queryType": "range",
                "startRfc3339": "now-6h",
                "endRfc3339": "now",
            },
            "why": "a rising farm-wide frame time explains risk that is not one node's fault",
        }),
        (12.8, TOOL_RESULT, "scout", {
            "tool": "query_prometheus",
            "call_id": "sc-3",
            "ok": True,
            "latency_ms": 902,
            "summary": "mean frame time 91s -> 214s over the last 90 minutes, "
                       "across every renderer and every node",
        }),
        (14.2, AGENT_THOUGHT, "scout", {
            "text": "Two different shapes here. Frame time is up everywhere at "
                    "once, which is not a per-shot problem. And TC/FA are the "
                    "heaviest sequences, so they cross the deadline first. "
                    "Handing the cause to Gaffer.",
        }),
        (15.6, CAPTION, "system", {
            "text": "SCOUT — 34 shots now projected past delivery",
            "duration_ms": 4600,
        }),

        # --- GAFFER: why? --------------------------------------------------
        (17.5, AGENT_START, "gaffer", {
            "role": "Gaffer",
            "goal": "Root-cause the slowdown from metrics, logs and traces",
        }),
        (18.6, AGENT_THOUGHT, "gaffer", {
            "text": "Farm-wide frame inflation with healthy node counts points "
                    "at a shared resource: the texture cache, the licence pool, "
                    "or the asset mount. Check all three before reading a log.",
        }),
        (20.1, TOOL_CALL, "gaffer", {
            "tool": "query_prometheus",
            "call_id": "gf-1",
            "args": {
                "datasourceUid": "grafanacloud-prom",
                "expr": "texture_cache_hit_ratio",
                "queryType": "range",
                "startRfc3339": "now-3h",
                "endRfc3339": "now",
            },
        }),
        (22.0, TOOL_RESULT, "gaffer", {
            "tool": "query_prometheus",
            "call_id": "gf-1",
            "ok": True,
            "latency_ms": 588,
            "summary": "hit ratio 0.93 -> 0.38 at 04:12, flat since. Licence pool "
                       "unchanged at 140/140 available.",
            "rows": [
                {"t": "03:40", "value": 0.93},
                {"t": "04:12", "value": 0.71},
                {"t": "04:40", "value": 0.44},
                {"t": "05:10", "value": 0.38},
            ],
        }),
        (23.8, AGENT_THOUGHT, "gaffer", {
            "text": "Cache collapse, not licence starvation — seats are all "
                    "free. A cold cache makes every bucket refetch over the "
                    "network before it can shade, which is the 2.4x on frame "
                    "times. Confirm in the logs.",
        }),
        (25.4, TOOL_CALL, "gaffer", {
            "tool": "query_loki_logs",
            "call_id": "gf-2",
            "args": {
                "datasourceUid": "grafanacloud-logs",
                "logql": "{service_name=\"render-farm\"} |= \"not in cache\"",
                "limit": 50,
                "startRfc3339": "now-1h",
            },
        }),
        (27.5, TOOL_RESULT, "gaffer", {
            "tool": "query_loki_logs",
            "call_id": "gf-2",
            "ok": True,
            "latency_ms": 1140,
            "summary": "312 matching lines in the last hour, 0 in the hour before",
            "rows": [
                {"line": "[arnold] WARNING | texture /assets/tc/env/dust_atlas_04.tx "
                         "not in cache, refetching from /mnt/assets (4.4s)"},
                {"line": "[karma] WARNING | texture /assets/fa/env/facade_brick_02_"
                         "displace.tx not in cache, refetching from /mnt/assets (3.1s)"},
                {"line": "[arnold] WARNING | texture cache hit ratio 0.38, tile "
                         "server thrashing, frame times inflated 2.4x"},
            ],
        }),
        (29.4, TOOL_CALL, "gaffer", {
            "tool": "find_error_pattern_logs",
            "call_id": "gf-3",
            "args": {"name": "shot-clock-sweep", "labels": {"service_name": "render-farm"}},
            "why": "Sift will surface any second fault hiding under the cache noise",
        }),
        (32.6, TOOL_RESULT, "gaffer", {
            "tool": "find_error_pattern_logs",
            "call_id": "gf-3",
            "ok": True,
            "latency_ms": 3070,
            "summary": "elevated pattern found: 'exit code 137 (killed)' on rn-047, "
                       "6 occurrences, 0 elsewhere on the farm",
            "rows": [
                {"line": "[arnold] ERROR | unable to allocate 4.8GB for BVH, aborting"},
                {"line": "[arnold] ERROR | render aborted at frame 0112 on rn-047, "
                         "exit code 137 (killed)"},
            ],
        }),
        (34.4, TOOL_CALL, "gaffer", {
            "tool": "query_prometheus",
            "call_id": "gf-4",
            "args": {
                "datasourceUid": "grafanacloud-prom",
                "expr": "topk(3, deriv(node_memory_bytes[15m]))",
                "queryType": "instant",
            },
        }),
        (36.3, TOOL_RESULT, "gaffer", {
            "tool": "query_prometheus",
            "call_id": "gf-4",
            "ok": True,
            "latency_ms": 517,
            "summary": "rn-047 climbing 1.9 GB/min in a sawtooth; next two nodes flat",
            "rows": [
                {"node": "rn-047", "value": "+1.94 GB/min"},
                {"node": "rn-112", "value": "+0.02 GB/min"},
                {"node": "rn-008", "value": "-0.01 GB/min"},
            ],
        }),
        (38.2, AGENT_THOUGHT, "gaffer", {
            "text": "Two independent faults, not one. A leak isolated to rn-047 "
                    "killing its render every 40 minutes, and a farm-wide cache "
                    "collapse. Neither explains the other. Both go in the note.",
        }),
        (40.0, CAPTION, "system", {
            "text": "GAFFER — two faults: rn-047 leaking, texture cache at 0.38",
            "duration_ms": 5000,
        }),

        # --- the vision check: the fault no metric can see ------------------
        (42.0, TOOL_CALL, "gaffer", {
            "tool": "get_panel_image",
            "call_id": "gf-5",
            "args": {"dashboardUid": "shotclock-farm", "panelId": 12},
            "why": "pull the dailies contact sheet for the shots Scout flagged",
        }),
        (44.4, TOOL_RESULT, "gaffer", {
            "tool": "get_panel_image",
            "call_id": "gf-5",
            "ok": True,
            "latency_ms": 2210,
            "summary": "PNG returned, 1440x810",
        }),
        (46.0, VISION_VERDICT, "gaffer", {
            "shot_id": "RC_0410",
            "frame": frame,
            "frame_number": 112,
            "verdict": "reject",
            "defect": "fireflies",
            "confidence": 0.91,
            "metrics_clean": True,
            "note": "Sampling fireflies across the specular highlights on the "
                    "roof furniture. The render reported success, frame time "
                    "was nominal, memory was nominal — nothing in Prometheus "
                    "or Loki says this plate is wrong. It is wrong.",
        }),
        (48.6, CAPTION, "system", {
            "text": "Every metric said this frame was fine. Only the picture disagrees.",
            "duration_ms": 5400,
        }),

        # --- PRODUCER: what does it cost? ----------------------------------
        (51.0, AGENT_START, "producer", {
            "role": "Producer",
            "goal": "Convert the delay into hours and dollars a supervisor can act on",
        }),
        (52.4, TOOL_CALL, "producer", {
            "tool": "query_prometheus",
            "call_id": "pr-1",
            "args": {
                "datasourceUid": "grafanacloud-prom",
                "expr": "sum(rate(shot_frames_completed_total[1h])) * 3600",
                "queryType": "range",
                "startRfc3339": "now-12h",
                "endRfc3339": "now",
            },
        }),
        (54.5, TOOL_RESULT, "producer", {
            "tool": "query_prometheus",
            "call_id": "pr-1",
            "ok": True,
            "latency_ms": 744,
            "summary": "throughput 1,880 frames/h before 04:12, 790 frames/h after "
                       "— a 58% loss sustained for 96 minutes",
        }),
        (56.2, AGENT_THOUGHT, "producer", {
            "text": "At 790 frames/hour the tail of the shot list lands 31 hours "
                    "past the date. Farm time is billed at $4.10 a node-hour and "
                    "the retries on rn-047 are pure waste.",
        }),
        (58.0, COSTING, "producer", {
            "shots_at_risk": 34,
            "shot_hours": 61.5,
            "node_hours": 4488,
            "cost_usd": 18400,
            "wasted_render_hours": 12.3,
            "delay_hours": 31.0,
            "lines": [
                {"label": "Shots projected late", "value": "34 of 1,200"},
                {"label": "Delivery slip if unfixed", "value": "31 h past 30 Sep"},
                {"label": "Throughput lost", "value": "58% (1,880 -> 790 frames/h)"},
                {"label": "Wasted render on rn-047", "value": "12.3 node-hours"},
                {"label": "Cost exposure", "value": "$18,400 at $4.10/node-h"},
            ],
        }),
        (60.2, CAPTION, "system", {
            "text": "PRODUCER — 31 hours past delivery, $18,400 exposed",
            "duration_ms": 5000,
        }),

        # --- FIRST AD: write it back ---------------------------------------
        (62.5, AGENT_START, "first_ad", {
            "role": "First AD",
            "goal": "Put the finding back into Grafana where the crew already looks",
        }),
        (63.8, AGENT_THOUGHT, "first_ad", {
            "text": "Two faults, one delivery risk, one incident. Open it at "
                    "sev-2, annotate the farm dashboard at 04:12 so the cache "
                    "collapse is marked on every panel, and pin the shot list.",
        }),
        (65.4, TOOL_CALL, "first_ad", {
            "tool": "create_incident",
            "call_id": "ad-1",
            "args": {
                "title": "Delivery risk: 34 shots past 30 Sep (cache collapse + rn-047 leak)",
                "severity": "minor",
                "roomPrefix": "shot-clock",
                "status": "active",
            },
        }),
        (68.1, TOOL_RESULT, "first_ad", {
            "tool": "create_incident",
            "call_id": "ad-1",
            "ok": True,
            "latency_ms": 2620,
            "summary": f"incident {INCIDENT_ID} created, room #shot-clock-3f21c9",
        }),
        (69.6, TOOL_CALL, "first_ad", {
            "tool": "add_activity_to_incident",
            "call_id": "ad-2",
            "args": {
                "incidentId": INCIDENT_ID,
                "body": "Cache hit ratio fell 0.93 -> 0.38 at 04:12; frame times "
                        "inflated 2.4x farm-wide. Separately rn-047 is leaking "
                        "1.9 GB/min and has OOM-killed 6 renders. RC_0410 frame "
                        "0112 rejected on look: fireflies, metrics clean.",
            },
        }),
        (71.4, TOOL_RESULT, "first_ad", {
            "tool": "add_activity_to_incident",
            "call_id": "ad-2",
            "ok": True,
            "latency_ms": 1180,
            "summary": "note added to incident timeline",
        }),
        (72.9, TOOL_CALL, "first_ad", {
            "tool": "create_annotation",
            "call_id": "ad-3",
            "args": {
                "dashboardUID": "shotclock-farm",
                "time": "04:12",
                "tags": ["shot-clock", "texture-cache", "delivery-risk"],
                "text": "Texture cache collapse — frame times 2.4x",
            },
        }),
        (74.6, TOOL_RESULT, "first_ad", {
            "tool": "create_annotation",
            "call_id": "ad-3",
            "ok": True,
            "latency_ms": 830,
            "summary": "annotation 4471 created on all panels",
        }),
        (76.0, TOOL_CALL, "first_ad", {
            "tool": "generate_deeplink",
            "call_id": "ad-4",
            "args": {"resourceType": "dashboard", "dashboardUid": "shotclock-farm",
                     "timeRange": {"from": "now-6h", "to": "now"}},
        }),
        (77.6, TOOL_RESULT, "first_ad", {
            "tool": "generate_deeplink",
            "call_id": "ad-4",
            "ok": True,
            "latency_ms": 240,
            "summary": DASHBOARD_URL,
        }),
        (79.2, WRITE_BACK, "first_ad", {
            "resource": "incident",
            "target": "Grafana Incident",
            "title": "Delivery risk: 34 shots past 30 Sep",
            "id": INCIDENT_ID,
            "url": INCIDENT_URL,
            "note": (
                "PRODUCTION NOTE — 05:48, 4 days to delivery\n\n"
                "Two unrelated faults are between us and the date.\n\n"
                "1. The shared texture cache fell from 0.93 to 0.38 at 04:12 and "
                "has not recovered. Every bucket on every node is refetching over "
                "the network before it can shade, so frame times are up 2.4x "
                "farm-wide. Throughput is down 58%. This is the whole delay.\n\n"
                "2. rn-047 is leaking 1.9 GB/min and has OOM-killed six renders. "
                "It is not why the farm is slow, but it has burnt 12.3 node-hours "
                "and will keep doing so. Drain it.\n\n"
                "3. RC_0410 frame 0112 came back clean on every metric and is "
                "unusable: fireflies through the specular highlights. Re-render "
                "the shot with a higher sample floor; the plate would otherwise "
                "have gone to the client.\n\n"
                "If the cache is restored within the hour we hold the date. If it "
                "is not, the tail of TC and FA lands 31 hours late."
            ),
        }),
        (80.6, WRITE_BACK, "first_ad", {
            "resource": "annotation",
            "target": "Render farm dashboard",
            "title": "Texture cache collapse marked at 04:12",
            "id": "4471",
            "url": DASHBOARD_URL,
        }),
        (82.0, CAPTION, "system", {
            "text": "FIRST AD — incident opened, dashboard annotated, note filed",
            "duration_ms": 5200,
        }),
        (84.5, RUN_END, "system", {
            "status": "ok",
            "headline": "34 shots at risk; cause found and written back to Grafana",
            "duration_s": 84.5,
            "tool_calls": 12,
            "writes": 3,
        }),
    ]


def build_events() -> list[Event]:
    return [
        Event(kind=kind, actor=actor, offset=offset, payload=payload, seq=index + 1)
        for index, (offset, kind, actor, payload) in enumerate(_beats())
    ]


def write_journal(path: Path | None = None) -> Path:
    """(Re)write the scripted journal to disk and return its path."""
    target = path or JOURNAL_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        for event in build_events():
            fh.write(event.to_json() + "\n")
    return target


def ensure_journal(path: Path | None = None) -> Path:
    """Write the scripted journal if it is missing or empty."""
    target = path or JOURNAL_PATH
    if not target.exists() or target.stat().st_size == 0:
        return write_journal(target)
    return target


if __name__ == "__main__":  # pragma: no cover
    written = write_journal()
    print(f"wrote {written} ({len(build_events())} events)")
    print(json.dumps(_beats()[0][3], indent=2))
