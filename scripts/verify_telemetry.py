"""Confirm the simulator's telemetry actually landed in Grafana Cloud.

Gate 0's real test is not "the exporter returned 200" but "a query comes back
with data". This queries the stack the way the agents will: through the Grafana
datasource proxy, with the service account token.

Run:  ./.venv/Scripts/python.exe scripts/verify_telemetry.py
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

#: The metric names the simulator claims to emit. OTel appends _total to
#: counters on the Prometheus side, which is exactly the sort of thing that
#: must be checked rather than assumed.
EXPECTED_METRICS = [
    "node_memory_bytes",
    "shot_frames_completed_total",
    "render_frame_duration_seconds",
    "render_job_status",
    "licence_pool_available",
    "texture_cache_hit_ratio",
    "queue_depth",
    "shots_at_risk",
    "shots_complete",
    "shots_failed",
    "farm_frames_per_hour",
]


def client() -> httpx.Client:
    return httpx.Client(
        base_url=os.environ["GRAFANA_URL"].rstrip("/"),
        headers={
            "Authorization": f"Bearer {os.environ['GRAFANA_SERVICE_ACCOUNT_TOKEN']}"
        },
        timeout=45.0,
    )


# A Grafana Cloud stack ships several datasources of the same type. Picking
# "the first loki" lands on alert-state-history, which is always empty and
# looks exactly like a broken ingest path. Pin the uids.
DATASOURCE_UIDS = {
    "prometheus": "grafanacloud-prom",
    "loki": "grafanacloud-logs",
    "tempo": "grafanacloud-traces",
}


def datasources(c: httpx.Client) -> dict[str, str]:
    """Resolve the intended datasource uids, confirming each really exists."""
    r = c.get("/api/datasources")
    r.raise_for_status()
    available = {ds["uid"] for ds in r.json()}
    return {k: v for k, v in DATASOURCE_UIDS.items() if v in available}


def promql(c: httpx.Client, uid: str, expr: str) -> list:
    now = int(time.time() * 1000)
    body = {
        "queries": [
            {
                "refId": "A",
                "datasource": {"type": "prometheus", "uid": uid},
                "expr": expr,
                "instant": True,
            }
        ],
        "from": str(now - 15 * 60 * 1000),
        "to": str(now),
    }
    r = c.post("/api/ds/query", json=body)
    r.raise_for_status()
    frames = r.json()["results"]["A"].get("frames", [])
    return [f for f in frames if f.get("data", {}).get("values", [[]])[0]]


def logql(c: httpx.Client, uid: str, expr: str) -> int:
    now = time.time_ns()
    body = {
        "queries": [
            {
                "refId": "A",
                "datasource": {"type": "loki", "uid": uid},
                "expr": expr,
                "queryType": "range",
                "maxLines": 20,
            }
        ],
        "from": str((now - 15 * 60 * 1_000_000_000) // 1_000_000),
        "to": str(now // 1_000_000),
    }
    r = c.post("/api/ds/query", json=body)
    r.raise_for_status()
    frames = r.json()["results"]["A"].get("frames", [])
    return sum(len(f.get("data", {}).get("values", [[]])[0]) for f in frames)


def main() -> int:
    ok = True
    with client() as c:
        ds = datasources(c)
        print(f"\ndatasources: {ds}\n")
        if "prometheus" not in ds:
            print("no prometheus datasource found")
            return 1

        print("METRICS")
        for metric in EXPECTED_METRICS:
            try:
                frames = promql(c, ds["prometheus"], metric)
            except Exception as exc:  # noqa: BLE001
                print(f"  FAIL  {metric:34s} {type(exc).__name__}: {exc}")
                ok = False
                continue
            series = len(frames)
            if series:
                print(f"  PASS  {metric:34s} {series} series")
            else:
                print(f"  FAIL  {metric:34s} no data")
                ok = False

        if "loki" in ds:
            print("\nLOGS")
            for label, expr in (
                ("all renderer logs", '{service_name="render-farm"}'),
                ("warnings+errors", '{service_name="render-farm"} |~ "(WARNING|ERROR)"'),
            ):
                try:
                    n = logql(c, ds["loki"], expr)
                except Exception as exc:  # noqa: BLE001
                    print(f"  FAIL  {label:34s} {type(exc).__name__}: {exc}")
                    ok = False
                    continue
                print(f"  {'PASS' if n else 'FAIL'}  {label:34s} {n} lines")
                ok = ok and bool(n)

    print("\nGate 0 telemetry check:", "PASS\n" if ok else "INCOMPLETE\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
