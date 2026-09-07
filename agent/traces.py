"""Read the farm's traces, the one signal that carries shot AND node together.

Why this is Python and not an MCP tool: the Grafana MCP server's only route to
Tempo is `find_slow_requests`, which runs a Sift investigation. On this stack
that returns

    creating investigation: investigation failed: There was an internal error
    running your investigation.

so the trace pillar was ingested and never read -- a metrics-and-logs demo
wearing a three-signal claim. The datasource proxy has no such problem, and
`readings.py` already proves the pattern against Prometheus, so traces go the
same way: TraceQL to find the frame, then the trace API for its spans.

What makes the trace worth reading at all is that it is not a time series. The
shot-scoped metrics deliberately carry no `node` label -- crossing node with
shot would be 8,000 series against a 10k budget -- so metrics can say a shot is
slow and logs can say a node is unhealthy, but neither can say where inside a
single frame the time actually went. The span tree can, and it carries both
labels for free.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import httpx

from agent.mcp import TEMPO_UID

#: Spans the renderer emits, in pipeline order.
STAGES = ("scene_load", "texture_fetch", "render", "denoise", "write")


class NoTraces(RuntimeError):
    """No trace matched. Raised rather than defaulted, like StaleTelemetry."""


@dataclass
class SpanBreakdown:
    name: str
    seconds: float
    share: float
    errored: bool = False
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass
class FrameTrace:
    trace_id: str
    shot_id: str
    node: str
    frame: int
    renderer: str
    sequence: str
    total_seconds: float
    spans: list[SpanBreakdown]

    @property
    def worst(self) -> SpanBreakdown | None:
        """The errored span, or the longest one if nothing errored."""
        errored = [s for s in self.spans if s.errored]
        pool = errored or [s for s in self.spans if s.name in STAGES]
        return max(pool, key=lambda s: s.seconds) if pool else None

    def summary(self) -> str:
        w = self.worst
        head = (
            f"Trace {self.trace_id[:16]} - {self.shot_id} frame {self.frame:04d} on "
            f"{self.node} ({self.renderer}), {self.total_seconds:.1f}s end to end."
        )
        if not w:
            return head
        stages = ", ".join(
            f"{s.name} {s.seconds:.1f}s ({s.share:.0%})"
            for s in self.spans
            if s.name in STAGES
        )
        cache = w.attributes.get("texture.cache_hit_ratio")
        cache_note = f" The span records texture.cache_hit_ratio={cache}." if cache else ""
        return (
            f"{head} Breakdown: {stages}. The only span marked error is "
            f"`{w.name}`, {w.seconds:.1f}s or {w.share:.0%} of the frame.{cache_note} "
            f"Metrics cannot show this: they carry no node label, and they cannot "
            f"see inside a frame at all."
        )


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=os.environ["GRAFANA_URL"].rstrip("/"),
        headers={
            "Authorization": f"Bearer {os.environ['GRAFANA_SERVICE_ACCOUNT_TOKEN']}"
        },
        timeout=40.0,
    )


def _attrs(raw: list[dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    for a in raw or []:
        value = a.get("value") or {}
        if value:
            out[a["key"]] = str(next(iter(value.values())))
    return out


def _search(c: httpx.Client, query: str, hours: int = 1, limit: int = 20) -> list[dict]:
    now = int(time.time() * 1000)
    body = {
        "queries": [
            {
                "refId": "A",
                "datasource": {"type": "tempo", "uid": TEMPO_UID},
                "queryType": "traceql",
                "query": query,
                "limit": limit,
            }
        ],
        "from": str(now - hours * 3600 * 1000),
        "to": str(now),
    }
    r = c.post("/api/ds/query", json=body)
    r.raise_for_status()
    result = r.json().get("results", {}).get("A", {})
    if result.get("error"):
        raise NoTraces(result["error"])
    frames = result.get("frames") or []
    if not frames:
        return []
    schema = [f.get("name") for f in frames[0].get("schema", {}).get("fields", [])]
    values = frames[0].get("data", {}).get("values", [])
    if not values:
        return []
    return [dict(zip(schema, row)) for row in zip(*values)]


def read_frame_trace() -> dict:
    """Pull the span tree for the slowest frame whose trace contains an error.

    Takes no arguments, for the same reason `price_delivery_risk` takes none:
    the agent decides *when* to look at a trace, not which trace proves its
    point. It reads the slowest errored frame in the last hour, or the slowest
    frame at all if nothing errored.

    Returns:
        The trace id, the shot and node it belongs to, the per-stage
        breakdown, and a sentence summarising where the time went.
    """
    with _client() as c:
        rows = _search(
            c, '{ resource.service.name = "render-farm" && status = error }'
        )
        if not rows:
            rows = _search(c, '{ resource.service.name = "render-farm" }')
        if not rows:
            raise NoTraces(
                "no traces in the last hour. Is the farm simulator running?"
            )

        rows.sort(key=lambda r: float(r.get("traceDuration") or 0), reverse=True)
        trace_id = rows[0]["traceID"]
        r = c.get(f"/api/datasources/proxy/uid/{TEMPO_UID}/api/traces/{trace_id}")
        r.raise_for_status()
        batches = r.json().get("batches") or []

    spans: list[SpanBreakdown] = []
    parent: dict[str, str] = {}
    total = 0.0
    for batch in batches:
        for scope in batch.get("scopeSpans") or batch.get(
            "instrumentationLibrarySpans", []
        ):
            for s in scope.get("spans", []):
                seconds = (
                    int(s["endTimeUnixNano"]) - int(s["startTimeUnixNano"])
                ) / 1e9
                attrs = _attrs(s.get("attributes"))
                errored = s.get("status", {}).get("code") == "STATUS_CODE_ERROR"
                if s["name"] == "render_frame":
                    total = seconds
                    parent = attrs
                else:
                    spans.append(
                        SpanBreakdown(s["name"], seconds, 0.0, errored, attrs)
                    )

    denom = total or sum(s.seconds for s in spans) or 1.0
    for s in spans:
        s.share = s.seconds / denom
    spans.sort(key=lambda s: STAGES.index(s.name) if s.name in STAGES else 99)

    trace = FrameTrace(
        trace_id=trace_id,
        shot_id=parent.get("shot.id", "unknown"),
        node=parent.get("render.node", "unassigned"),
        frame=int(float(parent.get("render.frame", 0) or 0)),
        renderer=parent.get("render.renderer", "unknown"),
        sequence=parent.get("shot.sequence", ""),
        total_seconds=total or denom,
        spans=spans,
    )
    worst = trace.worst
    return {
        "summary": trace.summary(),
        "trace_id": trace.trace_id,
        "shot_id": trace.shot_id,
        "node": trace.node,
        "frame": trace.frame,
        "renderer": trace.renderer,
        "total_seconds": round(trace.total_seconds, 2),
        "spans": [
            {
                "name": s.name,
                "seconds": round(s.seconds, 2),
                "share": round(s.share, 4),
                "errored": s.errored,
                **({"cache_hit_ratio": s.attributes["texture.cache_hit_ratio"]}
                   if "texture.cache_hit_ratio" in s.attributes else {}),
            }
            for s in trace.spans
        ],
        "worst_span": worst.name if worst else None,
    }
