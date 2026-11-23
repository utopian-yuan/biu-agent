"""把 Agent 工具接到一个带身份和资料范围的只读 HTTP 接口。"""

import asyncio
from argparse import ArgumentParser
from secrets import token_urlsafe

import httpx
from agentscope.state import AgentState
from agentscope.tool import Toolkit
from fastapi import FastAPI, Request

from biu_agent.identity import Principal, Problem, install_http_errors
from biu_agent.tools import Inputs, tool
from examples.tickets.corpus import Passage, catalog, excerpt
from tutorials.s02_tools.code import invoke


def material_api(sessions, grants, requests):
    app = FastAPI()
    install_http_errors(app)

    def scope(request):
        actor = sessions.get(request.headers.get("Authorization"))
        requests.append((request.url.path, actor.display_name if actor else "未认证"))
        if actor is None:
            raise Problem(401, "session_expired", "请在宿主重新登录。")
        return grants.get((actor.tenant_id, actor.user_id), set())

    @app.get("/materials")
    async def list_materials(request: Request):
        return [{"id": r["id"], "sections": r["sections"]} for r in catalog(scope(request))]

    @app.get("/excerpt")
    async def read_material(request: Request, id: str, section: str):
        return excerpt(id, section, scope(request))

    return app


def adapters(client, evidence):
    async def get(path, **params):
        response = await client.get(path, params=params)
        if response.is_error:
            error = response.json()["error"]
            raise Problem(response.status_code, error["code"], error["message"], error["retryable"])
        return response.json()

    async def list_materials():
        return await get("/materials")

    async def read_material(id, section):
        return await get("/excerpt", id=id, section=section)

    def record(row):
        evidence[row["source"]] = row["sha256"]

    return Toolkit(
        tools=[
            tool(list_materials, Inputs, "列出当前账号可读材料的段号。"),
            tool(read_material, Passage, "按资料 ID 和段号读原文、出处与版本。", on_result=record),
        ]
    )


async def main(case):
    actor = Principal("展具组", "u-7", "1", "", display_name="小舟")
    authorization = "Bearer " + token_urlsafe(24)
    sessions = {authorization: actor}
    owner = (actor.tenant_id, actor.user_id)
    grants = {owner: {"receiving"} if case == "limited" else {"receiving", "acceptance"}}
    requests, evidence = [], {}
    app = material_api(sessions, grants, requests)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://materials.test", headers={"Authorization": authorization}
    ) as client:
        toolkit = adapters(client, evidence)
        state = AgentState()
        listing = await invoke(toolkit, state, "list-1", "list_materials", {})
        print("目录内容", listing.content[0].text)
        if case == "revoke-after-list":
            grants[owner].remove("acceptance")
        if case == "expired-session":
            sessions.clear()
        result = await invoke(
            toolkit, state, "read-1", "read_material", {"id": "acceptance", "section": "A2"}
        )
        print("详情内容", result.content[0].text)
    print("服务端观察", requests)
    print("已登记出处", list(evidence))


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument(
        "--case", choices=["full", "limited", "revoke-after-list", "expired-session"], default="full"
    )
    asyncio.run(main(parser.parse_args().case))
