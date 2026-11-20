"""Registration, isolation and migration through the public workspace endpoints."""

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from biu_agent import Principal
from biu_agent.db import Database
from biu_agent.session import Sessions
from examples.tickets.accounts import digest, hash_password
from examples.tickets.config import defaults

from .test_material_workspace import API, LOGIN, answer, client_at, login, new_session, save


def test_accounts_keep_history_capabilities_and_guide_separate(tmp_path):
    second = {"username": "小川", "password": "independent-fixture"}
    with client_at(tmp_path) as c:
        login(c)
        first_path = new_session(c)
        first, _ = answer(c, first_path)
        c.post(API + "workspace/guide")
        skill = c.post(
            API + "workspace/skills/add",
            json={"values": {"name": "我的核对法", "description": "核对展具", "content": "逐件记录。"}},
        ).json()["id"]
        assert c.post("/auth/register", json=second).status_code == 201
        assert c.get(API + "bootstrap").status_code == 401
        login(c, False, credentials=second)
        config = c.get(API + "workspace").json()
        assert not config["guided"] and skill not in config["skills"]
        assert not c.get(API + "sessions").json()["items"]
        assert c.get(first_path).status_code == 404
        assert c.get(f"{first_path}/turns/{first['turn_id']}/sources/acceptance/A1").status_code == 404
        assert c.post(first_path + "/delete", json={}).status_code == 404
        assert c.post(API + f"workspace/skills/{skill}/delete", json={"revision": 1}).status_code == 404
        assert save(c, "agents", config["agents"]["materials"], sources=["receiving"]).is_success
        second_turn, _ = answer(c, new_session(c))
        assert {r["id"] for r in second_turn["evidence"]} == {"receiving"}
        c.post("/auth/logout")
        login(c, False)
        config = c.get(API + "workspace").json()
        assert config["guided"] and skill in config["skills"] and c.get(first_path).is_success
        assert set(config["agents"]["materials"]["sources"]) == {"receiving", "acceptance", "purchase"}


def test_concurrent_duplicate_registration_creates_one_account(tmp_path):
    with client_at(tmp_path) as c, ThreadPoolExecutor(max_workers=2) as pool:
        attempts = [pool.submit(c.post, "/auth/register", json=LOGIN) for _ in range(2)]
        assert sorted(call.result().status_code for call in attempts) == [201, 409]
        assert c.get(API + "bootstrap").status_code == 401
        login(c, False)
        assert set(c.get(API + "workspace").json()["agents"]) == {"materials", "receiving"}


@pytest.mark.parametrize("version", [1, 2])
def test_existing_account_migrates_with_sessions_settings_and_password(tmp_path, version):
    account_id = "original-account"
    token = "migration-browser-token"
    salt = b"fictional-test-salt"
    with sqlite3.connect(tmp_path / "workspace.sqlite3") as db:
        db.executescript("""
        CREATE TABLE owner(singleton INTEGER PRIMARY KEY,id TEXT,name TEXT,salt BLOB,password_hash BLOB,epoch INTEGER,guided INTEGER);
        CREATE TABLE logins(digest TEXT PRIMARY KEY,expires INTEGER);
        CREATE TABLE deployment_policy(id INTEGER PRIMARY KEY,value TEXT);
        CREATE TABLE resources(kind TEXT,id TEXT,data TEXT,revision INTEGER,PRIMARY KEY(kind,id));
        """)
        db.execute(f"PRAGMA user_version={version}")
        db.execute(
            "INSERT INTO owner VALUES(1,?,?,?,?,3,1)",
            (account_id, LOGIN["username"], salt, hash_password(LOGIN["password"], salt)),
        )
        db.execute("INSERT INTO logins VALUES(?,4000000000000)", (digest(token),))
        if version == 2:
            db.execute("CREATE TABLE agent_epochs(id TEXT PRIMARY KEY,revision INTEGER)")
            db.execute("INSERT INTO agent_epochs VALUES('materials',2)")
        data = defaults("")
        data["agents"]["materials"]["name"] = "原有资料助手"
        db.executemany(
            "INSERT INTO resources VALUES(?,?,?,4)",
            [(kind, k, json.dumps(row)) for kind, items in data.items() for k, row in items.items()],
        )
    epoch = "3.2" if version == 2 else "3"

    async def existing_history():
        sessions = Sessions(Database(tmp_path / "core.sqlite3"))
        await sessions.initialize()
        reader = Principal("local", account_id, "3", token, LOGIN["username"], {"materials": epoch})
        sid = await sessions.create(reader, "materials", "teaching")
        turn = await sessions.start(reader, sid, str(uuid4()), "核对展具签收", None)
        await sessions.finish(turn, "已保存的教程回答")
        return API + "sessions/" + sid

    saved = asyncio.run(existing_history())
    with client_at(tmp_path) as c:
        c.cookies.set("biu_workspace", token, domain="localhost.local", path="/")
        assert c.get(API + "bootstrap").json()["display_name"] == LOGIN["username"]
        login(c, False)
        conf = c.get(API + "workspace").json()
        assert c.get(saved).json()["turns"][0]["answer_text"] == "已保存的教程回答"
        assert conf["guided"] and conf["agents"]["materials"]["name"] == "原有资料助手"
        assert conf["agents"]["materials"]["revision"] == 4
        path = new_session(c)
        with sqlite3.connect(tmp_path / "core.sqlite3") as db:
            assert db.execute(
                "SELECT user_id,access_epoch FROM sessions WHERE id=?", (path.split("/")[-1],)
            ).fetchone() == (account_id, epoch)
    with client_at(tmp_path) as c:
        login(c, False)
        assert c.get(path).is_success
    assert len(list(tmp_path.glob(f"workspace.sqlite3.v{version}-*.bak"))) == 1
