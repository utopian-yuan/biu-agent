"""Use the published tutorial sources through the same endpoints as the workspace."""

import hashlib
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from agentscope.message import TextBlock
from agentscope.model import ChatResponse
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from biu_agent import Settings
from biu_agent.db import Database
from biu_agent.session import SCHEMA, Sessions
from examples.tickets.app import create_materials
from examples.tickets.corpus import DATA
from examples.tickets.teaching_model import TeachingModel

API = "/assistant/api/"
ORIGIN = "http://localhost"
LOGIN = {"username": "小禾", "password": "fixture-materials-only"}
QUESTION = "样品灯和导向牌分别到货了吗，有没有验收依据？请逐项引用原文。"


def client_at(directory, model=None, grants=None):
    return TestClient(
        create_materials(Settings(directory, model or TeachingModel()), grants=grants),
        base_url=ORIGIN,
        headers={"origin": ORIGIN},
    )


def login(c, register=True, credentials=None):
    credentials = credentials or LOGIN
    if register:
        assert c.post("/auth/register", json=credentials).status_code == 201
    assert c.post("/auth/login", json=credentials).is_success
    c.headers["x-csrf-token"] = c.get(API + "bootstrap").json()["csrf_token"]


def new_session(c, agent="materials", model=None):
    values = {"agent_id": agent}
    if model:
        values["model_id"] = model
    res = c.post(API + "sessions", json=values)
    assert res.status_code == 201, res.text
    return API + "sessions/" + res.json()["session_id"]


def answer(
    c, path, question=QUESTION, *, request_id=None, source=None, model=None, success=True, attachments=()
):
    body = {"request_id": request_id or str(uuid4())}
    if model:
        body["model_id"] = model
    url = path + "/turns"
    if source:
        url += "/" + source + "/regenerate"
    else:
        body["text"] = question
        if attachments:
            body["attachments"] = attachments
    stream = c.post(url, json=body)
    assert stream.is_success, stream.text
    if success:
        assert "event: turn.done" in stream.text, stream.text
    snapshot = c.get(path).json()["turns"]
    if source:
        started = next(json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith("data: "))
        for row in snapshot:
            if row["turn_id"] == started["turn_id"] or source in (row["turn_id"], row["root_turn_id"]):
                return row, stream
        raise AssertionError("找不到重新生成的原问题")
    return snapshot[-1], stream


def editable(resource):
    return {
        key: value for key, value in resource.items() if key not in {"id", "revision", "origin", "has_secret"}
    }


def save(c, kind, resource, **changes):
    return c.post(
        API + f"workspace/{kind}/{resource['id']}/save",
        json={"revision": resource["revision"], "values": {**editable(resource), **changes}},
    )


def test_first_account_cookie_origin_and_logout(tmp_path):
    with client_at(tmp_path) as c:
        assert c.get("/").url.path == "/login"
        assert c.get(API + "workspace").status_code == 401
        assert c.post("/auth/login", json=LOGIN).status_code == 401
        assert (
            c.post("/auth/register", json=LOGIN, headers={"origin": "https://example.invalid"}).status_code
            == 403
        )
        created = c.post("/auth/register", json=LOGIN)
        assert created.status_code == 201 and "set-cookie" not in created.headers
        assert c.get(API + "bootstrap").status_code == 401 and c.get("/").url.path == "/login"
        assert c.post("/auth/register", json={**LOGIN, "password": "different-password"}).status_code == 409
        login(c, False)
        assert c.get("/").url.path == "/assistant/chat/"
        assert c.post(API + "sessions", json={}, headers={"x-csrf-token": ""}).status_code == 403
        token = c.cookies.get("biu_workspace")
        assert c.post("/auth/logout").is_success
        assert c.get(API + "sessions", headers={"cookie": "biu_workspace=" + token}).status_code == 401
        assert c.post("/auth/login", json={**LOGIN, "password": "invalid-example"}).status_code == 401
        login(c, False)
        with sqlite3.connect(tmp_path / "workspace.sqlite3") as db:
            salt, stored = db.execute("select salt,password_hash from accounts").fetchone()
            assert len(salt) == 16 and stored != LOGIN["password"].encode()


@pytest.mark.parametrize(
    ("agent", "expected"),
    [("materials", {"purchase", "receiving", "acceptance"}), ("receiving", {"purchase", "receiving"})],
)
def test_scoped_tools_and_versioned_evidence(tmp_path, agent, expected):
    with client_at(tmp_path) as c:
        login(c)
        assert {
            d["id"] for d in c.get(API + "materials", params={"agent_id": agent}).json()["items"]
        } == expected
        path = new_session(c, agent)
        turn, _ = answer(c, path)
        assert {r["id"] for r in turn["evidence"]} == expected
        context = turn["context"]
        assert context["history_questions"] == 0 and context["skills"] == ["材料核对"]
        assert context["calls"].count("read_material") == 2 * len(expected)
        assert "list_materials" in context["calls"] and "Skill" in context["calls"]
        for ref in turn["evidence"]:
            original = c.get(f"{path}/turns/{turn['turn_id']}/sources/{ref['id']}/{ref['section']}").json()
            raw = (DATA / (ref["id"] + ".md")).read_bytes()
            assert original["text"] in raw.decode() and ref["sha256"] == hashlib.sha256(raw).hexdigest()
            assert ref["source"] in turn["answer_text"]
        assert "导向牌" in turn["answer_text"] and "缺少" in turn["answer_text"]
        if agent == "receiving":
            assert "通电" not in turn["answer_text"]
            assert c.get(API + "materials/acceptance/A1?agent_id=receiving").status_code == 404
            assert c.get(f"{path}/turns/{turn['turn_id']}/sources/acceptance/A1").status_code == 404


def test_history_feedback_regenerate_idempotency_and_restart(tmp_path):
    with client_at(tmp_path) as c:
        login(c)
        path = new_session(c)
        first, _ = answer(c, path)
        rid = str(uuid4())
        second, _ = answer(c, path, "那导向牌呢？", request_id=rid)
        assert second["context"]["history_questions"] == 1
        replay = c.post(path + "/turns", json={"request_id": rid, "text": "那导向牌呢？"})
        assert replay.json()["turn_id"] == second["turn_id"]
        assert (
            c.post(
                path + "/turns", json={"request_id": rid, "text": "那导向牌呢？", "model_id": "teaching"}
            ).status_code
            == 409
        )
        regenerated, _ = answer(c, path, source=first["turn_id"])
        assert (
            regenerated["input_text"] == first["input_text"]
            and regenerated["source_turn"] == first["turn_id"]
        )
        assert regenerated["context"]["history_questions"] == 0
        vote = f"{path}/turns/{regenerated['turn_id']}/feedback"
        for value in ["up", "down", None, "up"]:
            assert c.post(vote, json={"value": value}).json()["value"] == value
            assert c.get(path).json()["turns"][0]["feedback"] == value
        assert c.post(path + "/rename", json={"title": "展具签收核对"}).is_success
    with client_at(tmp_path) as c:
        login(c, False)
        assert [s["title"] for s in c.get(API + "sessions?q=展具").json()["items"]] == ["展具签收核对"]
        restored = c.get(path).json()
        assert (
            len(restored["turns"]) == 2
            and restored["turns"][1]["turn_id"] == second["turn_id"]
            and restored["turns"][0]["turn_id"] == regenerated["turn_id"]
            and restored["turns"][0]["feedback"] == "up"
            and not restored["actions"]
        )
        assert c.post(path + "/delete", json={}).is_success and c.get(path).status_code == 404
        assert not c.get(API + "sessions").json()["items"]
        assert c.post(vote, json={"value": "down"}).status_code == 404


def test_removed_grants_invalidate_links_and_regeneration(tmp_path):
    with client_at(tmp_path) as c:
        login(c)
        path = new_session(c)
        other = new_session(c, "receiving")
        t, _ = answer(c, path)
        conf = c.get(API + "workspace").json()
        agent = conf["agents"]["materials"]
        assert save(c, "agents", agent, sources=["purchase", "receiving"]).is_success
        assert c.get(path).status_code == 409
        assert c.get(other).is_success
        assert [s["id"] for s in c.get(API + "sessions").json()["items"]] == [other.split("/")[-1]]
        assert c.get(f"{path}/turns/{t['turn_id']}/sources/acceptance/A1").status_code == 409
        assert (
            c.post(f"{path}/turns/{t['turn_id']}/regenerate", json={"request_id": str(uuid4())}).status_code
            == 409
        )
        assert save(c, "agents", agent).status_code == 409
        path = new_session(c)
        assert "acceptance" not in {r["id"] for r in answer(c, path)[0]["evidence"]}
        reader = conf["tools"]["read_material"]
        assert save(c, "tools", reader, enabled=False).is_success
        t, _ = answer(c, new_session(c))
        assert not t["evidence"] and "read_material" not in t["context"]["calls"]
        assert "依据不足" in t["answer_text"]
    with client_at(tmp_path, grants=[]) as c:
        login(c, False)
        assert c.get(API + "materials").json() == {"items": []}
        t, _ = answer(c, new_session(c))
        assert not t["evidence"] and not t["context"]["sources"]


def test_settings_selection_skill_bindings_and_secrets(tmp_path):
    with client_at(tmp_path) as c:
        login(c)

        def create(kind, values):
            return c.post(API + f"workspace/{kind}/add", json={"values": values})

        key = "material-test-secret"
        model = create(
            "models",
            {"name": "试用模型", "url": "http://localhost:13499/v1", "model": "example", "secret": key},
        ).json()["id"]
        skill = create(
            "skills", {"name": "核对数量", "description": "核对数量", "content": "列明采购数量"}
        ).json()["id"]
        mcp = create("mcps", {"name": "演练目录", "url": "http://localhost:13499/mcp", "secret": key}).json()[
            "id"
        ]
        configs = c.get(API + "workspace")
        assert key not in configs.text and "sealed" not in configs.text
        conf = configs.json()
        assert conf["models"][model]["has_secret"] and conf["mcps"][mcp]["has_secret"]
        agent = editable(conf["agents"]["receiving"])
        agent.update(name="展具助手", skills=[skill], model_id=model)
        aid = create("agents", agent).json()["id"]
        path = new_session(c, aid)
        assert c.get(path).json()["model_id"] == model
        t, _ = answer(c, path, model="teaching")
        assert t["model_label"] == "教学演示" and t["context"]["skills"] == ["核对数量"]
        assert c.get(path).json()["model_id"] == "teaching"
        assert c.post(API + f"workspace/models/{model}/delete", json={"revision": 1}).status_code == 409
        assert save(c, "mcps", conf["mcps"][mcp], url="http://localhost:13500/mcp").is_success
        assert not c.get(API + "workspace").json()["mcps"][mcp]["has_secret"]
        with sqlite3.connect(tmp_path / "workspace.sqlite3") as db:
            raw = db.execute("select data from resources where id=?", (model,)).fetchone()[0]
            assert key not in raw and json.loads(raw)["sealed"]


def test_unread_citation_is_never_published(tmp_path):
    class InvalidCitation(TeachingModel):
        async def _call_api(self, model_name, messages, tools=None, **kwargs):
            return ChatResponse(content=[TextBlock(text="虚构结论 [acceptance#A1]")], is_last=True)

    with client_at(tmp_path) as c:
        login(c)
        # The SDK-compatible fixture replaces only model inference. The real server guards publication.
        with pytest.MonkeyPatch.context() as patch:
            from examples.tickets.workspace import Workspace

            patch.setattr(Workspace, "model", lambda self, row: InvalidCitation())
            t, stream = answer(c, new_session(c, "receiving"), success=False)
        assert t["error_code"] == "unverified_source" and t["answer_text"] is None
        assert "虚构结论" not in stream.text and "event: text.delta" not in stream.text


async def test_earlier_database_is_preserved_when_migrated(tmp_path):
    path = tmp_path / "core.sqlite3"
    db = Database(path)
    await db.initialize(SCHEMA)
    await db.write(lambda con: con.execute("INSERT INTO sessions VALUES('s','local','u','1',10)"))
    await Sessions(db).initialize()
    await Sessions(db).initialize()
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT id,updated_at FROM sessions").fetchone() == ("s", 10)
        assert con.execute("PRAGMA user_version").fetchone()[0] == 5
    assert len(list(tmp_path.glob("*.bak"))) == 1


@pytest.mark.live
def test_configured_model_on_both_material_scopes(tmp_path):
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    settings = Settings.from_env()
    settings.data_dir = tmp_path
    settings.turn_seconds = 120
    with TestClient(create_materials(settings), base_url=ORIGIN, headers={"origin": ORIGIN}) as c:
        login(c)
        for agent in ("materials", "receiving"):
            t, _ = answer(c, new_session(c, agent, "environment"))
            print(
                f"{agent} · {t['model_label']} · {', '.join(r['source'] for r in t['evidence'])}\n{t['answer_text']}"
            )
            assert t["model_label"] == settings.model.model and "导向牌" in t["answer_text"]
            assert "receiving#R2" in t["answer_text"]
            if agent == "receiving":
                assert (
                    all(r["id"] != "acceptance" for r in t["evidence"])
                    and "acceptance#" not in t["answer_text"]
                )
            else:
                assert "acceptance#A1" in t["answer_text"]


def test_changed_source_reopens_only_at_same_version_and_bounds_context(tmp_path, monkeypatch):
    from shutil import copytree

    from examples.tickets import corpus

    documents = tmp_path / "documents"
    copytree(DATA, documents)
    monkeypatch.setattr(corpus, "DATA", documents)
    with client_at(tmp_path) as c:
        login(c)
        path = new_session(c)
        first, _ = answer(c, path)
        receiving = documents / "receiving.md"
        original = receiving.read_text()
        receiving.write_text(original.replace("包装完好", "外箱已检查"))
        link = f"{path}/turns/{first['turn_id']}/sources/receiving/R1"
        assert c.get(link).json()["error"]["code"] == "source_changed"
        fresh, _ = answer(c, path, source=first["turn_id"])
        assert "外箱已检查" in fresh["answer_text"] and "包装完好" not in fresh["answer_text"]
        receiving.write_text(original.replace("包装完好", "长" * 4100))
        bounded, _ = answer(c, path)
        assert "receiving#R1" not in {r["source"] for r in bounded["evidence"]}
        assert "长" * 10 not in bounded["answer_text"]


def test_permissions_revoked_while_model_is_working(tmp_path, monkeypatch):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from examples.tickets.workspace import Workspace

    entered, release = Event(), Event()

    class PausingModel(TeachingModel):
        async def _call_api(self, model_name, messages, tools=None, **kwargs):
            if not entered.is_set():
                entered.set()
                await asyncio.to_thread(release.wait, 5)
            return await super()._call_api(model_name, messages, tools, **kwargs)

    monkeypatch.setattr(Workspace, "model", lambda self, row: PausingModel())
    with client_at(tmp_path) as c, ThreadPoolExecutor(max_workers=1) as worker:
        login(c)
        path = new_session(c)
        resource = c.get(API + "workspace").json()["agents"]["materials"]
        pending = worker.submit(c.post, path + "/turns", json={"text": QUESTION, "request_id": str(uuid4())})
        try:
            assert entered.wait(3)
            assert save(c, "agents", resource, sources=["receiving"]).is_success
        finally:
            release.set()
        result = pending.result(8)
        assert "scope_changed" in result.text and "event: text.delta" not in result.text
        assert c.get(path).status_code == 409


def test_acceptance_only_never_implies_delivery(tmp_path):
    with client_at(tmp_path, grants=["acceptance"]) as c:
        login(c)
        path = new_session(c)
        turn, _ = answer(c, path)
        assert {r["source"] for r in turn["evidence"]} == {"acceptance#A1", "acceptance#A2"}
        assert "签收" not in turn["answer_text"]
        with sqlite3.connect(tmp_path / "core.sqlite3") as db:
            recorded = dict(
                db.execute(
                    "SELECT name,COUNT(*) FROM spans WHERE turn_id=? AND status='ok' GROUP BY name",
                    (turn["turn_id"],),
                )
            )
        assert recorded["tool.read_material"] == 2 and recorded["tool.list_materials"] == 1
        assert recorded["turn"] == 1 and recorded["model.call"] >= 2
