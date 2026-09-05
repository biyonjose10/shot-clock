"""Ship farm telemetry to Grafana Cloud over OTLP.

One exporter carries all three signals to a single endpoint with a single
credential: metrics to Mimir (queried with PromQL), logs to Loki (LogQL) and
traces to Tempo. That is the whole reason this path was chosen over Prometheus
remote_write, which needs snappy-compressed protobuf assembled by hand.

CARDINALITY
    Grafana Cloud's free tier allows roughly 10k active series. The label sets
    below never cross ``node`` with ``shot_id``: doing so would be 200 x 40 =
    8,000 series for a single metric. Node-scoped metrics carry ``node`` only,
    shot-scoped metrics carry ``shot_id`` only, and farm-wide gauges carry no
    identifying label at all.

METRIC NAMES
    OpenTelemetry counters gain a ``_total`` suffix when translated to
    Prometheus, so ``shot_frames_completed`` is queried as
    ``shot_frames_completed_total``. Gauges and histograms keep their names.
    Verify in Explore rather than trusting this comment.
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Iterable

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

SERVICE_NAME = "render-farm"
EXPORT_INTERVAL_MS = 15_000

log = logging.getLogger("renderfarm")


class MissingCredentials(RuntimeError):
    """Raised when the Grafana Cloud OTLP settings are absent or incomplete."""


def _auth_header() -> str:
    """Grafana Cloud OTLP authenticates as base64(instance_id:token)."""
    instance = os.environ.get("GRAFANA_OTLP_INSTANCE_ID", "").strip()
    token = os.environ.get("GRAFANA_OTLP_TOKEN", "").strip()
    if not instance or not token:
        raise MissingCredentials(
            "GRAFANA_OTLP_INSTANCE_ID and GRAFANA_OTLP_TOKEN must be set in .env"
        )
    raw = f"{instance}:{token}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def _base_endpoint() -> str:
    endpoint = os.environ.get("GRAFANA_OTLP_ENDPOINT", "").strip().rstrip("/")
    if not endpoint:
        raise MissingCredentials("GRAFANA_OTLP_ENDPOINT must be set in .env")
    return endpoint


class FarmTelemetry:
    """Bind a :class:`sim.farm.Farm` to Grafana Cloud.

    Metrics are observable: on each export the SDK calls back into the farm and
    reads its current state, so the simulator never has to push.
    """

    def __init__(self, farm) -> None:
        self.farm = farm
        self._headers = {"Authorization": _auth_header()}
        self._base = _base_endpoint()
        self._resource = Resource.create(
            {
                "service.name": SERVICE_NAME,
                "service.namespace": "shot-clock",
                "deployment.environment": os.environ.get("SHOT_CLOCK_ENV", "sim"),
            }
        )
        self._meter_provider: MeterProvider | None = None
        self._tracer_provider: TracerProvider | None = None
        self._logger_provider: LoggerProvider | None = None

    # -- setup -------------------------------------------------------------
    def start(self) -> None:
        self._start_metrics()
        self._start_traces()
        self._start_logs()

    def _start_metrics(self) -> None:
        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(
                endpoint=f"{self._base}/v1/metrics", headers=self._headers
            ),
            export_interval_millis=EXPORT_INTERVAL_MS,
        )
        self._meter_provider = MeterProvider(
            resource=self._resource, metric_readers=[reader]
        )
        metrics.set_meter_provider(self._meter_provider)
        meter = metrics.get_meter("shot-clock.farm")

        # Node-scoped: labelled by `node` only.
        meter.create_observable_gauge(
            "node_memory_bytes",
            callbacks=[self._observe_node_memory],
            description="Resident memory per render node",
            unit="By",
        )
        # Shot-scoped: labelled by `shot_id` plus low-cardinality descriptors.
        meter.create_observable_counter(
            "shot_frames_completed",
            callbacks=[self._observe_frames_completed],
            description="Frames finished per shot",
        )
        meter.create_observable_gauge(
            "render_frame_duration_seconds",
            callbacks=[self._observe_frame_duration],
            description="Mean seconds per frame for the current shot",
            unit="s",
        )
        meter.create_observable_gauge(
            "render_job_status",
            callbacks=[self._observe_job_status],
            description="1 while a shot is rendering, 0 when it is not",
        )
        # Farm-wide: no identifying labels at all.
        meter.create_observable_gauge(
            "licence_pool_available",
            callbacks=[self._observe_licences],
            description="Renderer licences free in the pool",
        )
        meter.create_observable_gauge(
            "texture_cache_hit_ratio",
            callbacks=[self._observe_cache_ratio],
            description="Farm-wide texture cache hit ratio, 0-1",
        )
        meter.create_observable_gauge(
            "queue_depth",
            callbacks=[self._observe_queue_depth],
            description="Shots waiting for a free node",
        )
        # Delivery position. These are farm-wide (one series each) on purpose:
        # most at-risk shots are in the 1,160-shot backlog and never appear in
        # the per-shot metrics, so without these the agents can only see the 40
        # shots currently in flight and would badly understate the exposure.
        meter.create_observable_gauge(
            "shots_at_risk",
            callbacks=[self._observe_shots_at_risk],
            description="Shots projected to finish after the delivery date",
        )
        meter.create_observable_gauge(
            "shots_complete",
            callbacks=[self._observe_shots_complete],
            description="Shots delivered",
        )
        meter.create_observable_gauge(
            "shots_failed",
            callbacks=[self._observe_shots_failed],
            description="Shots that exhausted their retries",
        )
        meter.create_observable_gauge(
            "farm_frames_per_hour",
            callbacks=[self._observe_frames_per_hour],
            description="Farm-wide throughput, the number the delivery date depends on",
        )

    def _start_traces(self) -> None:
        self._tracer_provider = TracerProvider(resource=self._resource)
        self._tracer_provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=f"{self._base}/v1/traces", headers=self._headers
                )
            )
        )
        trace.set_tracer_provider(self._tracer_provider)

    def _start_logs(self) -> None:
        self._logger_provider = LoggerProvider(resource=self._resource)
        self._logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(
                OTLPLogExporter(endpoint=f"{self._base}/v1/logs", headers=self._headers)
            )
        )
        handler = LoggingHandler(
            level=logging.INFO, logger_provider=self._logger_provider
        )
        log.setLevel(logging.INFO)
        log.addHandler(handler)

    # -- metric callbacks --------------------------------------------------
    def _observe_node_memory(self, options: CallbackOptions) -> Iterable[Observation]:
        for node in self.farm.node_states():
            yield Observation(
                int(node.memory_used_gb * 1024**3),
                {"node": node.node, "health": str(node.health)},
            )

    def _observe_frames_completed(
        self, options: CallbackOptions
    ) -> Iterable[Observation]:
        for shot in self.farm.shot_states():
            yield Observation(shot.frames_done, self._shot_labels(shot))

    def _observe_frame_duration(
        self, options: CallbackOptions
    ) -> Iterable[Observation]:
        for shot in self.farm.shot_states():
            yield Observation(shot.mean_frame_seconds, self._shot_labels(shot))

    def _observe_job_status(self, options: CallbackOptions) -> Iterable[Observation]:
        for shot in self.farm.shot_states():
            labels = self._shot_labels(shot)
            labels["status"] = str(shot.status)
            labels["at_risk"] = "true" if shot.at_risk else "false"
            yield Observation(1 if str(shot.status) == "rendering" else 0, labels)

    def _observe_licences(self, options: CallbackOptions) -> Iterable[Observation]:
        yield Observation(self.farm.summary().licences_available, {})

    def _observe_cache_ratio(self, options: CallbackOptions) -> Iterable[Observation]:
        yield Observation(self.farm.summary().texture_cache_hit_ratio, {})

    def _observe_queue_depth(self, options: CallbackOptions) -> Iterable[Observation]:
        yield Observation(self.farm.summary().queue_depth, {})

    def _observe_shots_at_risk(self, options: CallbackOptions) -> Iterable[Observation]:
        yield Observation(self.farm.summary().shots_at_risk, {})

    def _observe_shots_complete(self, options: CallbackOptions) -> Iterable[Observation]:
        yield Observation(self.farm.summary().shots_complete, {})

    def _observe_shots_failed(self, options: CallbackOptions) -> Iterable[Observation]:
        yield Observation(self.farm.summary().shots_failed, {})

    def _observe_frames_per_hour(self, options: CallbackOptions) -> Iterable[Observation]:
        yield Observation(self.farm.summary().frames_per_hour, {})

    @staticmethod
    def _shot_labels(shot) -> dict[str, str]:
        # NOTE: deliberately no `node` label here. See the cardinality note.
        return {
            "shot_id": shot.shot_id,
            "sequence": shot.sequence,
            "renderer": shot.renderer,
            "artist": shot.artist,
        }

    # -- teardown ----------------------------------------------------------
    def shutdown(self) -> None:
        for provider in (
            self._meter_provider,
            self._tracer_provider,
            self._logger_provider,
        ):
            if provider is not None:
                try:
                    provider.shutdown()
                except Exception:  # noqa: BLE001 - best effort on exit
                    pass
