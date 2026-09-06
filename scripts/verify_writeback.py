"""Prove the Grafana write path works, before an agent spends tokens on it.

First AD's whole value is that it writes back to Grafana rather than only
reading. That capability depends on the stack and the service account, not on
the model, so it can and should be tested directly -- finding out from a failed
agent run is slower, costs tokens, and confuses a model failure with a
permissions failure.

Everything created here is tagged and then deleted, except the dashboard, which
is the one the demo uses.

    ./.venv/Scripts/python.exe scripts/verify_writeback.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

PASS, FAIL, SKIP = "  PASS  ", "  FAIL  ", "  SKIP  "


def client() -> httpx.Client:
    return httpx.Client(
        base_url=os.environ["GRAFANA_URL"].rstrip("/"),
        headers={
            "Authorization": f"Bearer {os.environ['GRAFANA_SERVICE_ACCOUNT_TOKEN']}"
        },
        timeout=45.0,
    )


def check_annotation(c: httpx.Client) -> bool:
    now = int(time.time() * 1000)
    r = c.post(
        "/api/annotations",
        json={
            "time": now - 600_000,
            "timeEnd": now,
            "tags": ["shot-clock", "render-farm", "writeback-check"],
            "text": "Texture cache collapse detected by Shot Clock",
        },
    )
    if r.status_code not in (200, 201):
        print(f"{FAIL}create_annotation      HTTP {r.status_code} {r.text[:120]}")
        return False
    ann_id = r.json().get("id")
    print(f"{PASS}create_annotation      id={ann_id}, tagged and timestamped")
    c.delete(f"/api/annotations/{ann_id}")
    return True


def check_dashboard(c: httpx.Client) -> bool:
    """Create the farm dashboard the agent will later annotate and snapshot."""
    dashboard = {
        "uid": "shot-clock-farm",
        "title": "Shot Clock - Render Farm",
        "tags": ["shot-clock"],
        "timezone": "browser",
        "time": {"from": "now-6h", "to": "now"},
        "panels": [
            {
                "id": 1,
                "type": "timeseries",
                "title": "Frame duration by shot",
                "gridPos": {"h": 9, "w": 12, "x": 0, "y": 0},
                "targets": [
                    {"refId": "A", "expr": "render_frame_duration_seconds"}
                ],
                "datasource": {"type": "prometheus", "uid": "grafanacloud-prom"},
            },
            {
                "id": 2,
                "type": "timeseries",
                "title": "Texture cache hit ratio",
                "gridPos": {"h": 9, "w": 12, "x": 12, "y": 0},
                "targets": [{"refId": "A", "expr": "texture_cache_hit_ratio"}],
                "datasource": {"type": "prometheus", "uid": "grafanacloud-prom"},
            },
            {
                "id": 3,
                "type": "timeseries",
                "title": "Farm throughput (frames/hour)",
                "gridPos": {"h": 8, "w": 12, "x": 0, "y": 9},
                "targets": [{"refId": "A", "expr": "farm_frames_per_hour"}],
                "datasource": {"type": "prometheus", "uid": "grafanacloud-prom"},
            },
            {
                "id": 4,
                "type": "timeseries",
                "title": "Licences free / queue depth",
                "gridPos": {"h": 8, "w": 12, "x": 12, "y": 9},
                "targets": [
                    {"refId": "A", "expr": "licence_pool_available"},
                    {"refId": "B", "expr": "queue_depth"},
                ],
                "datasource": {"type": "prometheus", "uid": "grafanacloud-prom"},
            },
        ],
    }
    r = c.post(
        "/api/dashboards/db",
        json={"dashboard": dashboard, "overwrite": True, "message": "Shot Clock farm view"},
    )
    if r.status_code not in (200, 201):
        print(f"{FAIL}update_dashboard       HTTP {r.status_code} {r.text[:160]}")
        return False
    body = r.json()
    print(f"{PASS}update_dashboard       {os.environ['GRAFANA_URL']}{body.get('url','')}")
    return True


def check_snapshot(c: httpx.Client) -> bool:
    """Snapshots are the embed path: Grafana Cloud has no anonymous access."""
    # A snapshot needs a real dashboard payload; an empty one is rejected with
    # "Dashboard not found". Snapshot the farm dashboard we just wrote.
    got = c.get("/api/dashboards/uid/shot-clock-farm")
    if got.status_code != 200:
        print(f"{FAIL}create_snapshot        could not read the farm dashboard")
        return False
    payload = got.json()["dashboard"]
    payload["time"] = {"from": "now-1h", "to": "now"}
    r = c.post(
        "/api/snapshots",
        json={"dashboard": payload, "name": "Shot Clock - incident snapshot", "expires": 86400},
    )
    if r.status_code not in (200, 201):
        print(f"{FAIL}create_snapshot        HTTP {r.status_code} {r.text[:120]}")
        return False
    body = r.json()
    key = body.get("key")
    print(f"{PASS}create_snapshot        {body.get('url') or body.get('deleteUrl','')}")
    if key:
        c.delete(f"/api/snapshots/{key}")
    return True


def check_incident(c: httpx.Client) -> bool | None:
    # The plugin id is `grafana-irm-app`. Grafana merged Incident and OnCall
    # into IRM and the old `grafana-incident-app` id 404s with "Plugin not
    # found" -- which this check was reading as "not installed on this stack",
    # so a working incident path was being reported as skipped.
    r = c.post(
        "/api/plugins/grafana-irm-app/resources/api/v1/IncidentsService.CreateIncident",
        json={"title": "Shot Clock write-back check", "severity": "minor", "roomPrefix": "shot-clock"},
    )
    if r.status_code == 404:
        print(f"{SKIP}create_incident        Incident app not installed on this stack")
        return None
    if r.status_code not in (200, 201):
        print(f"{FAIL}create_incident        HTTP {r.status_code} {r.text[:120]}")
        return False
    print(f"{PASS}create_incident        {str(r.json())[:100]}")
    return True


def main() -> int:
    print("\nGrafana write-back capability\n" + "-" * 62)
    with client() as c:
        results = [check_annotation(c), check_dashboard(c), check_snapshot(c)]
        incident = check_incident(c)
    print("-" * 62)
    core = all(results)
    if core:
        print("Core write path works. First AD can record an investigation.")
        if incident is None:
            print("Incidents unavailable; annotation + dashboard + snapshot cover it.\n")
        return 0
    print("Core write path is broken; check the service account role is Admin.\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
