"""Exercise the real SDK's model and MCP protocols against tutorial-only loopback fixtures."""

import json

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .material_server import serve_local
from .test_material_workspace import API, answer, client_at, editable, login, new_session


def test_selected_model_calls_only_the_bound_read_tool(tmp_path):
    catalog = FastMCP("教程资料目录", stateless_http=True)
    invoked = []

    @catalog.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
    def batch_number() -> str:
        """查询展具目录批次。"""
        invoked.append("batch_number")
        return "展具目录批次为 MX-07"

    @catalog.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
    def list_editions() -> str:
        """查询目录版本。"""
        return "第一版"

    @catalog.tool(annotations=ToolAnnotations(readOnlyHint=False))
    def mark_reviewed() -> str:
        """为本次测试模拟登记审阅。"""
        return "模拟操作"

    gateway = FastAPI()
    seen_models = []
    advertised = []

    @gateway.post("/v1/chat/completions")
    async def model(request: Request):
        req = await request.json()
        seen_models.append(req["model"])
        names = [item["function"]["name"] for item in req["tools"]]
        advertised.extend(names)
        tool = next(name for name in names if name.endswith("__batch_number"))
        has_result = any(m["role"] == "tool" and "MX-07" in str(m["content"]) for m in req["messages"])
        if has_result:
            delta = {"role": "assistant", "content": "展具目录批次为 MX-07。"}
            reason = "stop"
        else:
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "read-1",
                        "index": 0,
                        "type": "function",
                        "function": {"name": tool, "arguments": "{}"},
                    }
                ],
            }
            reason = "tool_calls"

        def frames():
            for payload, finish in [(delta, None), ({}, reason)]:
                chunk = {
                    "id": "material-reply",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": req["model"],
                    "choices": [{"index": 0, "delta": payload, "finish_reason": finish}],
                }
                yield "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(frames(), media_type="text/event-stream")

    with (
        serve_local(catalog.streamable_http_app()) as mcp,
        serve_local(gateway) as provider,
        client_at(tmp_path) as c,
    ):
        login(c)

        def add(kind, **values):
            response = c.post(API + f"workspace/{kind}/add", json={"values": values})
            assert response.is_success, response.text
            return response.json()["id"]

        connection = add("mcps", name="目录", url=mcp + "/mcp", tools=["batch_number", "mark_reviewed"])
        result = c.post(API + f"connections/{connection}/test", json={})
        assert result.is_success and set(result.json()["tools"]) == {"batch_number", "list_editions"}, (
            result.text
        )
        selected = add("models", name="教程本地接口", model="catalog-example", url=provider + "/v1")
        profile = editable(c.get(API + "workspace").json()["agents"]["receiving"])
        aid = add("agents", **{**profile, "name": "目录助手", "mcps": [connection]})
        path = new_session(c, aid, "teaching")
        turn, _ = answer(c, path, "查询本期展具目录的批次编号。", model=selected)
        assert invoked == ["batch_number"] and seen_models == ["catalog-example"] * 2
        assert not any(n.endswith(("__mark_reviewed", "__list_editions")) for n in advertised)
        assert turn["model_label"] == "catalog-example" and "MX-07" in turn["answer_text"]
        assert sum(n.endswith("__batch_number") for n in turn["context"]["calls"]) == 1
        assert turn["evidence"] == []
