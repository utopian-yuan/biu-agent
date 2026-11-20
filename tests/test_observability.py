import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from biu_agent import Settings
from examples.tickets.app import create_materials
from examples.tickets.teaching_model import TeachingModel
from integrations.langfuse import LangfuseExport, send_scores
from integrations.langfuse.__main__ import material_case

from .test_material_workspace import ORIGIN, answer, login, new_session


class Captured(SpanExporter):
    def __init__(self):
        self.spans = []

    def export(self, spans):
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS


def test_real_material_turns_export_only_metadata_and_fixed_scores(tmp_path):
    capture = Captured()
    sink = LangfuseExport(capture)
    app = create_materials(Settings(tmp_path, TeachingModel(), export_span=sink))
    scores = []
    with TestClient(app, base_url=ORIGIN, headers={"origin": ORIGIN}) as c:
        login(c)
        for aid in ("materials", "receiving"):
            t, _ = answer(c, new_session(c, aid))
            with sqlite3.connect(tmp_path / "core.sqlite3") as db:
                db.row_factory = sqlite3.Row
                report, spans = material_case(db, t["turn_id"], aid)
                assert report["passed"]
                scores.append(sink.item("biu-materials-v1", "run-a", "教程", report, spans))
    assert sink.close() and {s["value"] for s in scores} == {1}
    assert scores[0]["traceId"] != scores[1]["traceId"]
    assert {"turn", "tool.read_material", "model.call", "experiment.item"} <= {s.name for s in capture.spans}
    raw = encode_spans(capture.spans).SerializeToString()
    for term in ("小禾", "导向牌", "通电与数量检查", "fixture-materials-only"):
        assert term.encode() not in raw


def test_partial_score_batch_reports_rejected_items(monkeypatch):
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://scores.example.invalid")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "test-public")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "test-secret")
    scores = [{"name": "material_references_valid", "value": 1}, {"name": "material_references_valid", "value": 0}]
    calls = []

    def partial(url, *, auth, json, timeout):
        calls.append((url, json))
        return httpx.Response(207, request=httpx.Request("POST", url), json={"accepted": 1, "rejected": 1})

    monkeypatch.setattr(httpx, "post", partial)
    with pytest.raises(ValueError, match="拒绝 1 项"):
        send_scores(scores)
    assert calls == [("https://scores.example.invalid/api/public/scores", scores)]
