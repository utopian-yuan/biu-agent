import pytest

from biu_agent.db import Database


async def test_schema_version_and_empty_install(tmp_path):
    db = Database(tmp_path / "unknown.sqlite3")
    await db.write(lambda c: c.execute("CREATE TABLE precious(value)"))
    with pytest.raises(RuntimeError):
        await db.initialize("CREATE TABLE unexpected(id)")
    assert await db.read(lambda c: c.execute("SELECT name FROM sqlite_master").fetchone()[0]) == "precious"
    await db.write(lambda c: c.execute("PRAGMA user_version=99"))
    with pytest.raises(RuntimeError):
        await db.initialize("CREATE TABLE unexpected(id)")


def test_existing_host_serves_the_shared_chat_and_material_records(tmp_path):
    from uuid import uuid4

    from agentscope.tool import Toolkit
    from fastapi.testclient import TestClient

    from biu_agent import Principal, Settings, create_app
    from biu_agent.tools import Inputs, tool
    from examples.tickets.corpus import catalog
    from examples.tickets.teaching_model import TeachingModel

    async def identity(_request):
        return Principal("local", "tutorial-reader", "1", "session-fixture", "材料读者")

    def build(ctx):
        async def list_materials():
            documents = catalog({"receiving"})
            ctx.show_items(
                [{"kind": "document", "id": d["id"], "label": d["title"], "version": 1} for d in documents]
            )
            return documents

        return Toolkit(tools=[tool(list_materials, Inputs, "列出授权的材料")])

    app = create_app(Settings(tmp_path, TeachingModel()), identity, build, {})
    with TestClient(app, base_url="http://localhost", headers={"origin": "http://localhost"}) as c:
        ui = c.get("/chat/")
        assert ui.is_success and "workspace.js" in ui.text
        boot = c.get("/api/bootstrap").json()
        assert not boot["workspace"] and boot["model_name"] == "教学演示"
        c.headers["x-csrf-token"] = boot["csrf_token"]
        sid = c.post("/api/sessions", json={}).json()["session_id"]
        url = "/api/sessions/" + sid
        res = c.post(url + "/turns", json={"text": "列出材料", "request_id": str(uuid4())})
        assert "event: turn.done" in res.text
        assert c.get(url).json()["turns"][0]["display"]["items"][0]["label"] == "到货记录"
