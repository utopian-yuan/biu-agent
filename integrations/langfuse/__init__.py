"""Export only biu-agent's explicit metadata, without a global OTel provider."""

import base64
import json
import os
import threading
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExportResult
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import SpanContext, Status, StatusCode, TraceFlags


def span_context(trace_id, span_id):
    return SpanContext(
        UUID(trace_id).int,
        UUID(span_id).int & ((1 << 64) - 1),
        False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )


def destination():
    names = ("LANGFUSE_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
    missing = [k for k in names if not os.getenv(k)]
    if missing:
        raise ValueError("请填写可选追踪配置 " + "、".join(missing))
    endpoint, public, secret = (os.environ[k] for k in names)
    url = urlsplit(endpoint)
    local = url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not (url.scheme == "https" or local)
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("LANGFUSE_BASE_URL 请使用 HTTPS 或本机环回服务地址。")
    return endpoint.rstrip("/"), public, secret


def send_scores(scores):
    endpoint, public, secret = destination()
    response = httpx.post(endpoint + "/api/public/scores", auth=(public, secret), json=scores, timeout=10)
    response.raise_for_status()
    if response.status_code == 207:
        counts = response.json()
        raise ValueError(
            f"Langfuse 接收 {counts['accepted']} 项评分，拒绝 {counts['rejected']} 项。请核对逐题结果。"
        )


class CheckedOTLPExporter(OTLPSpanExporter):
    """Remember transport failures so a batch import can be resumed from its local report."""

    failed = False

    def export(self, spans):
        result = super().export(spans)
        if result != SpanExportResult.SUCCESS:
            self.failed = True
        return result


class LangfuseExport:
    def __init__(self, exporter):
        self.exporter = exporter
        self.processor = BatchSpanProcessor(
            exporter,
            max_queue_size=512,
            max_export_batch_size=32,
            schedule_delay_millis=250,
            export_timeout_millis=2500,
        )
        self.resource = Resource({"service.name": "biu-agent"})
        self.scope = InstrumentationScope("biu_agent")

    @classmethod
    def from_env(cls, *, timeout=2):
        endpoint, public, secret = destination()
        basic = base64.b64encode((public + ":" + secret).encode()).decode()
        return cls(
            CheckedOTLPExporter(
                endpoint=endpoint + "/api/public/otel/v1/traces",
                headers={"Authorization": "Basic " + basic, "x-langfuse-ingestion-version": "4"},
                timeout=timeout,
            )
        )

    def __call__(self, row):
        self.send(row, row["trace_id"], row["parent_id"], {})

    def send(self, row, trace_id, parent_id, attributes):
        attrs = {**attributes}
        for key in ("session_id", "turn_id", "action_id", "error_code"):
            if row.get(key) is not None:
                attrs["biu." + key] = row[key]
        attrs["biu.status"] = row["status"]
        if row["name"].startswith("tool."):
            attrs["langfuse.observation.type"] = "tool"
        if row.get("model_name"):
            attrs["langfuse.observation.type"] = "generation"
            attrs["langfuse.observation.model.name"] = row["model_name"]
            usage = {
                key: row[key + "_tokens"] for key in ("input", "output") if row[key + "_tokens"] is not None
            }
            if usage:
                attrs["langfuse.observation.usage_details"] = json.dumps(usage)
        start = row["started_at"] * 1_000_000
        span = ReadableSpan(
            name=row["name"],
            context=span_context(trace_id, row["id"]),
            parent=span_context(trace_id, parent_id) if parent_id else None,
            resource=self.resource,
            instrumentation_scope=self.scope,
            attributes=attrs,
            start_time=start,
            end_time=start + row["duration_ms"] * 1_000_000,
            status=Status(StatusCode.OK if row["status"] == "ok" else StatusCode.ERROR),
        )
        self.processor.on_end(span)

    def item(self, dataset, run, version, report, spans):
        # Each item has one trace. Preserve real parent links within every turn and confirmation.
        item = report["case_id"]
        trace_id = str(uuid5(NAMESPACE_URL, f"biu:{run}/{item}"))
        root_id = str(uuid5(UUID(trace_id), "item"))
        attrs = {
            "langfuse.experiment.id": run,
            "langfuse.experiment.name": version,
            "langfuse.experiment.dataset.id": dataset,
            "langfuse.experiment.item.id": item,
            "langfuse.experiment.item.root_observation_id": f"{span_context(trace_id, root_id).span_id:016x}",
            "langfuse.environment": "experiment",
        }
        for row in spans:
            self.send(row, trace_id, row["parent_id"] or root_id, attrs)
        start = min(s["started_at"] for s in spans)
        end = max(s["started_at"] + s["duration_ms"] for s in spans)
        result = {"passed": report["passed"], "errors": report["errors"]}
        root = {
            "id": root_id,
            "name": "experiment.item",
            "status": "ok" if report["passed"] else "error",
            "started_at": start,
            "duration_ms": end - start,
        }
        self.send(
            root,
            trace_id,
            None,
            {
                **attrs,
                "langfuse.observation.input": item,
                "langfuse.observation.output": json.dumps(result),
                "langfuse.experiment.item.expected_output": '{"passed":true}',
            },
        )
        # Stable identity and timestamp make importing the same local report safe to repeat.
        return {
            "id": str(uuid5(UUID(trace_id), "material_references_valid")),
            "traceId": UUID(trace_id).hex,
            "observationId": attrs["langfuse.experiment.item.root_observation_id"],
            "name": "material_references_valid",
            "dataType": "BOOLEAN",
            "value": int(report["passed"]),
            "environment": "experiment",
            "timestamp": datetime.fromtimestamp(start / 1000, UTC).isoformat(),
        }

    def close(self, timeout=3):
        # Some exporter implementations retry at shutdown. Bound how long a host waits.
        worker = threading.Thread(target=self.processor.shutdown, daemon=True)
        worker.start()
        worker.join(timeout)
        return not worker.is_alive() and not getattr(self.exporter, "failed", False)
