"""Prove both Grafana Cloud paths work before building anything on top of them.

Checks, in order:
  1. .env is present and every required variable is filled in.
  2. The OTLP endpoint accepts a real metric (writes ``shot_clock_preflight``).
  3. The service account token can read the Grafana API, and reports its role.
  4. The token can WRITE -- creates and deletes a throwaway annotation, because
     a token that can read but not write fails silently at demo time.

Run:  ./.venv/Scripts/python.exe scripts/preflight.py
"""
from __future__ import annotations

import base64
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = [
    "GRAFANA_OTLP_ENDPOINT",
    "GRAFANA_OTLP_INSTANCE_ID",
    "GRAFANA_OTLP_TOKEN",
    "GRAFANA_URL",
    "GRAFANA_SERVICE_ACCOUNT_TOKEN",
]

PASS = "  PASS  "
FAIL = "  FAIL  "


def _report(ok: bool, label: str, detail: str = "") -> bool:
    print(f"{PASS if ok else FAIL}{label}" + (f"  --  {detail}" if detail else ""))
    return ok


def check_env() -> bool:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return _report(False, ".env exists", f"missing: {env_file}")
    load_dotenv(env_file)
    missing = [k for k in REQUIRED if not os.environ.get(k, "").strip()]
    if missing:
        return _report(False, ".env complete", "empty: " + ", ".join(missing))
    return _report(True, ".env complete", f"{len(REQUIRED)} variables set")


def check_otlp() -> bool:
    """Send one real metric and force a flush, so a 4xx surfaces here."""
    from opentelemetry import metrics
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource

    base = os.environ["GRAFANA_OTLP_ENDPOINT"].strip().rstrip("/")
    auth = base64.b64encode(
        f"{os.environ['GRAFANA_OTLP_INSTANCE_ID'].strip()}:"
        f"{os.environ['GRAFANA_OTLP_TOKEN'].strip()}".encode()
    ).decode()

    exporter = OTLPMetricExporter(
        endpoint=f"{base}/v1/metrics", headers={"Authorization": f"Basic {auth}"}
    )
    reader = PeriodicExportingMetricReader(exporter, export_interval_millis=60_000)
    provider = MeterProvider(
        resource=Resource.create({"service.name": "shot-clock-preflight"}),
        metric_readers=[reader],
    )
    metrics.set_meter_provider(provider)
    provider.get_meter("preflight").create_counter("shot_clock_preflight").add(1)

    try:
        flushed = provider.force_flush(timeout_millis=20_000)
    except Exception as exc:  # noqa: BLE001 - report, do not crash the preflight
        return _report(False, "OTLP metric accepted", f"{type(exc).__name__}: {exc}")
    finally:
        try:
            provider.shutdown()
        except Exception:  # noqa: BLE001
            pass
    return _report(
        bool(flushed),
        "OTLP metric accepted",
        "wrote shot_clock_preflight_total" if flushed else "flush timed out",
    )


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=os.environ["GRAFANA_URL"].strip().rstrip("/"),
        headers={
            "Authorization": f"Bearer {os.environ['GRAFANA_SERVICE_ACCOUNT_TOKEN'].strip()}"
        },
        timeout=20.0,
    )


def check_grafana_read() -> bool:
    try:
        with _client() as c:
            r = c.get("/api/user")
    except Exception as exc:  # noqa: BLE001
        return _report(False, "Grafana API readable", f"{type(exc).__name__}: {exc}")
    if r.status_code != 200:
        return _report(
            False, "Grafana API readable", f"HTTP {r.status_code}: {r.text[:120]}"
        )
    body = r.json()
    return _report(
        True, "Grafana API readable", f"login={body.get('login')} org={body.get('orgId')}"
    )


def check_grafana_write() -> bool:
    """Create then delete an annotation. Read-only tokens fail silently later."""
    now = int(time.time() * 1000)
    payload = {
        "time": now,
        "timeEnd": now,
        "tags": ["shot-clock", "preflight"],
        "text": "Shot Clock preflight -- safe to delete",
    }
    try:
        with _client() as c:
            r = c.post("/api/annotations", json=payload)
            if r.status_code not in (200, 201):
                return _report(
                    False,
                    "Grafana API writable",
                    f"HTTP {r.status_code}: {r.text[:160]}",
                )
            ann_id = r.json().get("id")
            if ann_id:
                c.delete(f"/api/annotations/{ann_id}")
    except Exception as exc:  # noqa: BLE001
        return _report(False, "Grafana API writable", f"{type(exc).__name__}: {exc}")
    return _report(True, "Grafana API writable", "created and deleted an annotation")


def main() -> int:
    print("\nShot Clock preflight\n" + "-" * 60)
    if not check_env():
        print("\nFix .env first; the remaining checks need it.\n")
        return 1
    results = [check_otlp(), check_grafana_read(), check_grafana_write()]
    print("-" * 60)
    if all(results):
        print("All checks passed. Gate 0 ingest path is live.\n")
        return 0
    print("Some checks failed. Nothing downstream will work until they pass.\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
