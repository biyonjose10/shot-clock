"""Build a public dashboard snapshot with the farm data baked into it.

A snapshot made from the live dashboard keeps pointing at
`grafanacloud-prom`, so a judge without a login opens it and sees four empty
panels. This queries the demo window once and embeds the result in the
snapshot itself, so the page renders with no datasource and no auth.

    python -m scripts.make_snapshot            # print the new public URL
    python -m scripts.make_snapshot --dry-run  # build it, post nothing
"""

import argparse
import calendar
import json
import time
import urllib.parse
import urllib.request

DS = {"type": "datasource", "uid": "grafana"}

# The demo window: the farm collapsed, ran degraded for two hours, then
# recovered once the cache was resized. UTC.
WINDOW_FROM = "2026-09-07 18:00"
WINDOW_TO = "2026-09-07 20:35"
STEP = 30

# Panel id -> the queries to bake in. Panel 1 is topk-limited: the raw metric
# carries one series per shot and 444 of them is not a chart anyone can read.
# The eight slowest shots over the window, pinned by id so each renders as one
# continuous line. `topk` alone re-picks its winners at every step and yields a
# hundred-odd stubs instead.
SLOWEST = "OD_0820|MC_1280|MC_1430|HA_0190|MC_1190|MC_1090|MC_0800|MC_0270"

QUERIES = {
    1: [("A", f'render_frame_duration_seconds{{shot_id=~"{SLOWEST}"}}')],
    2: [("A", "texture_cache_hit_ratio")],
    3: [("A", "farm_frames_per_hour")],
    4: [("A", "licence_pool_available"), ("B", "queue_depth")],
}
TITLES = {1: "Frame duration - 8 slowest shots"}


def env():
    out = {}
    with open(".env", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def epoch(stamp):
    return calendar.timegm(time.strptime(stamp, "%Y-%m-%d %H:%M"))


def query_range(base, token, expr, start, end):
    url = f"{base}/api/datasources/proxy/uid/grafanacloud-prom/api/v1/query_range?" + urllib.parse.urlencode(
        {"query": expr, "start": start, "end": end, "step": STEP}
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.load(resp)["data"]["result"]


def label(metric, expr):
    """The name Grafana shows for this series.

    Prometheus hands back eight labels per series, and the full label set makes
    a legend nothing can be read out of. A shot is identified by its id.
    """
    if not metric:
        return expr
    if "shot_id" in metric:
        return metric["shot_id"]
    return metric.get("__name__", expr)


def frame(series, expr, ref_id):
    """One Prometheus series as a Grafana DataFrameJSON."""
    times = [int(float(t) * 1000) for t, _ in series["values"]]
    values = [float(v) for _, v in series["values"]]
    metric = series.get("metric", {})
    return {
        "schema": {
            "name": label(metric, expr),
            "refId": ref_id,
            "fields": [
                {"name": "Time", "type": "time", "typeInfo": {"frame": "time.Time"}},
                {
                    "name": "Value",
                    "type": "number",
                    "typeInfo": {"frame": "float64"},
                    "labels": {k: v for k, v in metric.items() if k != "__name__"},
                    "config": {"displayNameFromDS": label(metric, expr)},
                },
            ],
        },
        "data": {"values": [times, values]},
    }


def build(base, token):
    with open("dashboards/shot-clock-farm.json", encoding="utf-8") as fh:
        dash = json.load(fh)

    start, end = epoch(WINDOW_FROM), epoch(WINDOW_TO)
    total = 0

    for panel in dash["panels"]:
        panel["datasource"] = DS
        panel["title"] = TITLES.get(panel["id"], panel["title"])
        targets = []
        for ref_id, expr in QUERIES[panel["id"]]:
            result = query_range(base, token, expr, start, end)
            if not result:
                raise SystemExit(f"no data for {expr} in {WINDOW_FROM}..{WINDOW_TO}Z")
            frames = [frame(s, expr, ref_id) for s in result]
            total += sum(len(f["data"]["values"][0]) for f in frames)
            targets.append(
                {
                    "refId": ref_id,
                    "datasource": DS,
                    "queryType": "snapshot",
                    "snapshot": frames,
                }
            )
        panel["targets"] = targets

    dash["time"] = {
        "from": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(start)),
        "to": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(end)),
    }
    dash["timezone"] = "utc"
    dash["snapshot"] = {"originalUrl": "/d/shot-clock-farm"}
    dash.pop("id", None)
    return dash, total


def post(base, token, dash):
    body = json.dumps(
        {
            "dashboard": dash,
            "name": "Shot Clock - render farm, texture cache collapse",
            "expires": 0,
        }
    ).encode()
    req = urllib.request.Request(
        f"{base}/api/snapshots",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = env()
    base = cfg["GRAFANA_URL"].rstrip("/")
    token = cfg["GRAFANA_SERVICE_ACCOUNT_TOKEN"]

    dash, points = build(base, token)
    print(f"baked {points} points across {len(dash['panels'])} panels")
    if args.dry_run:
        print(f"payload {len(json.dumps(dash))} bytes - not posted")
        return

    out = post(base, token, dash)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
