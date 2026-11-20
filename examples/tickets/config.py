"""Account-scoped workspace configuration with stable IDs, revisions and server-side secrets."""

import ipaddress
import json
import os
import re
import tempfile
from typing import Annotated
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from biu_agent.agents.skills import bundled
from biu_agent.agents.tools import TOOL_INFO
from biu_agent.db import new_id
from biu_agent.identity import Problem, hidden, scope_changed

from .corpus import FILES

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_-]{1,64}$")]
Ids = Annotated[list[Identifier], Field(max_length=64)]


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    enabled: bool = True


class Model(Entry):
    model: Name
    url: str = Field(max_length=512)
    secret: str = Field(default="", max_length=4096, repr=False)


class SkillEntry(Entry):
    description: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=6000)


class Connection(Entry):
    url: str = Field(max_length=512)
    secret: str = Field(default="", max_length=4096, repr=False)
    tools: Ids = Field(default_factory=list)


class AgentEntry(Entry):
    allow_uploads: bool = True
    prompt: str = Field(default="", max_length=4000)
    model_id: Identifier
    sources: Ids
    tools: Ids
    skills: Ids
    mcps: Ids


TYPES = {"models": Model, "skills": SkillEntry, "tools": Entry, "mcps": Connection, "agents": AgentEntry}


def endpoint(url):
    try:
        parts = urlsplit(url)
        host = parts.hostname
        _ = parts.port
    except ValueError:
        raise Problem(422, "invalid_address", "服务地址格式无效。") from None
    try:
        local = host == "localhost" or bool(host and ipaddress.ip_address(host).is_loopback)
    except ValueError:
        local = False
    if (
        not host
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or not (parts.scheme == "https" or parts.scheme == "http" and local)
    ):
        raise Problem(422, "invalid_address", "请填写 HTTPS 地址，或本机服务的 HTTP 地址。")
    return url.rstrip("/")


def defaults(model_name):
    models = {"teaching": {"name": "教学演示", "model": "教学演示", "origin": "teaching", "enabled": True}}
    if model_name:
        models["environment"] = {
            "name": model_name,
            "model": model_name,
            "origin": "environment",
            "enabled": True,
        }
    preferred = "environment" if model_name else "teaching"
    base = {
        "enabled": True,
        "model_id": preferred,
        "tools": list(TOOL_INFO),
        "allow_uploads": True,
        "skills": ["source-review"],
        "mcps": [],
    }
    return {
        "models": models,
        "tools": {k: {"name": v[0], "enabled": True} for k, v in TOOL_INFO.items()},
        "skills": bundled(),
        "mcps": {},
        "agents": {
            "materials": {
                **base,
                "name": "材料助手",
                "prompt": "从已授权材料中逐项取证。",
                "sources": list(FILES),
            },
            "receiving": {
                **base,
                "name": "到货助手",
                "prompt": "核对采购和签收记录，说明仍缺少的依据。",
                "sources": ["purchase", "receiving"],
            },
        },
    }


def seed_account(db, user_id, model_name):
    db.executemany(
        "INSERT INTO resources(user_id,kind,id,data) VALUES(?,?,?,?)",
        [
            (user_id, kind, key, json.dumps(row, ensure_ascii=False))
            for kind, items in defaults(model_name).items()
            for key, row in items.items()
        ],
    )


def sync_builtin_tools(db):
    for account in db.execute("SELECT id FROM accounts"):
        uid = account[0]
        found = {
            r[0] for r in db.execute("SELECT id FROM resources WHERE user_id=? AND kind='tools'", (uid,))
        }
        added = [k for k in TOOL_INFO if k not in found]
        if not added:
            continue
        db.executemany(
            "INSERT INTO resources(user_id,kind,id,data) VALUES(?,'tools',?,?)",
            [
                (uid, k, json.dumps({"name": TOOL_INFO[k][0], "enabled": True}, ensure_ascii=False))
                for k in added
            ],
        )
        has_model = db.execute(
            "SELECT 1 FROM resources WHERE user_id=? AND kind='models' AND id='environment'", (uid,)
        ).fetchone()
        defaults_by_id = defaults("environment" if has_model else "")["agents"]
        original_tools = ["list_materials", "read_material"]
        previous_tools = [*original_tools, "search_materials", "shell"]
        for key, raw, revision in db.execute(
            "SELECT id,data,revision FROM resources WHERE user_id=? AND kind='agents' AND id IN ('materials','receiving')",
            (uid,),
        ).fetchall():
            record = json.loads(raw)
            looks_migrated = (
                revision == 2
                and record["tools"] == previous_tools
                and all(
                    record[k] == defaults_by_id[key][k]
                    for k in ("name", "prompt", "sources", "skills", "mcps", "enabled", "model_id")
                )
                and record.get("allow_uploads", True)
            )
            if (revision == 1 and record["tools"] in (original_tools, previous_tools)) or looks_migrated:
                record["tools"].extend(k for k in added if k not in record["tools"])
                db.execute(
                    "UPDATE resources SET data=?,revision=revision+1 WHERE user_id=? AND kind='agents' AND id=?",
                    (json.dumps(record, ensure_ascii=False), uid, key),
                )


def rows(db, user_id):
    data = {k: {} for k in TYPES}
    for kind, key, raw, rev in db.execute(
        "SELECT kind,id,data,revision FROM resources WHERE user_id=? ORDER BY rowid", (user_id,)
    ):
        data[kind][key] = {**json.loads(raw), "id": key, "revision": rev}
    return data


def account_record(db, p):
    row = db.execute("SELECT id,epoch,guided FROM accounts WHERE id=?", (p.user_id,)).fetchone()
    if not row or p.tenant_id != "local":
        raise hidden()
    if str(row["epoch"]) != p.access_epoch:
        raise scope_changed()
    return row


class Configuration:
    def __init__(self, accounts, real_model):
        self.accounts = accounts
        self.db = accounts.db
        self.real_model = real_model

    async def initialize(self):
        await self.accounts.initialize()
        await self.db.write(sync_builtin_tools)
        path = self.db.path.with_name("credentials.key")
        if not path.exists():
            with tempfile.NamedTemporaryFile(dir=path.parent) as keyfile:
                keyfile.write(Fernet.generate_key())
                keyfile.flush()
                try:
                    os.link(keyfile.name, path)
                except FileExistsError:
                    pass
        self.cipher = Fernet(path.read_bytes())
        if self.real_model:
            configured = defaults(self.real_model.model)["models"]["environment"]
            await self.db.write(
                lambda db: db.execute(
                    """INSERT INTO resources(user_id,kind,id,data)
                SELECT id,'models','environment',? FROM accounts WHERE true
                ON CONFLICT(user_id,kind,id) DO UPDATE SET data=excluded.data""",
                    (json.dumps(configured),),
                )
            )

    async def get(self, p, public=False):
        def fetch(db):
            account = account_record(db, p)
            data = rows(db, p.user_id)
            if not self.real_model:
                data["models"].pop("environment", None)
            if public:
                for kind in ("models", "mcps"):
                    for row in data[kind].values():
                        row["has_secret"] = bool(row.pop("sealed", "") or row.get("origin") == "environment")
                return {
                    **data,
                    "guided": bool(account["guided"]),
                    "source_names": FILES,
                    "tool_descriptions": {k: info[1] for k, info in TOOL_INFO.items()},
                }
            return data

        return await self.db.read(fetch)

    def token(self, row):
        return self.cipher.decrypt(row["sealed"].encode()).decode() if row.get("sealed") else ""

    async def save(self, p, kind, key, values, revision, delete=False, *, import_id=None):
        if kind not in TYPES:
            raise hidden()
        if key and not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", key):
            raise hidden()

        def write(db):
            account_record(db, p)
            all_rows = rows(db, p.user_id)
            if import_id and import_id in all_rows[kind]:
                raise Problem(409, "skill_exists", "此技能标识已存在，可编辑已有技能或更换文件中的 name。")
            current = all_rows[kind].get(key)
            if key and current is None:
                raise hidden()
            if current and current["revision"] != revision:
                raise Problem(409, "edit_conflict", "配置已更新，请重新打开。")
            if (
                kind == "models"
                and key in ("environment", "teaching")
                or kind == "tools"
                and (not key or delete)
            ):
                raise Problem(409, "fixed_resource", "这项配置由运行环境提供。")
            if delete:
                if kind == "agents" and key == "materials":
                    raise Problem(409, "default_agent", "请保留默认材料助手。")
                if kind != "agents":
                    for agent in all_rows["agents"].values():
                        if key == agent["model_id"] or key in agent.get(kind, []):
                            raise Problem(409, "in_use", "请先在使用它的智能体中解除关联。")
                db.execute(
                    "DELETE FROM resources WHERE user_id=? AND kind=? AND id=?", (p.user_id, kind, key)
                )
            else:
                try:
                    data = TYPES[kind].model_validate(values).model_dump()
                except ValidationError:
                    raise Problem(422, "invalid_config", "请检查必填内容、名称和选项。") from None
                if kind in ("models", "mcps"):
                    data["url"] = endpoint(data["url"])
                    secret = data.pop("secret")
                    if secret:
                        data["sealed"] = self.cipher.encrypt(secret.encode()).decode()
                    elif current and current.get("url") == data["url"] and current.get("sealed"):
                        data["sealed"] = current["sealed"]
                    if kind == "models":
                        data["origin"] = "custom"
                if kind == "agents":
                    for field in ("tools", "skills", "mcps", "sources"):
                        allowed = FILES if field == "sources" else all_rows[field]
                        if not set(data[field]) <= set(allowed):
                            raise Problem(422, "unknown_resource", "所选能力已失效，请刷新。")
                    if data["model_id"] not in all_rows["models"]:
                        raise Problem(422, "unknown_model", "请选择已配置的模型。")
                    if key == "materials" and not data["enabled"]:
                        raise Problem(409, "default_agent", "请保留默认材料助手。")
                rid = key or import_id or new_id()
                db.execute(
                    """INSERT INTO resources(user_id,kind,id,data) VALUES(?,?,?,?) ON CONFLICT(user_id,kind,id)
                     DO UPDATE SET data=excluded.data,revision=resources.revision+1""",
                    (p.user_id, kind, rid, json.dumps(data, ensure_ascii=False)),
                )
            if current and revoked(kind, current, None if delete else data):
                affected = (
                    [key]
                    if kind == "agents"
                    else [aid for aid, profile in all_rows["agents"].items() if key in profile[kind]]
                )
                db.executemany(
                    """INSERT INTO agent_epochs(user_id,id,revision) VALUES(?,?,1) ON CONFLICT(user_id,id)
                    DO UPDATE SET revision=agent_epochs.revision+1""",
                    [(p.user_id, aid) for aid in affected],
                )
            return {"id": key if delete else rid}

        return await self.db.write(write)


def revoked(kind, previous, updated):
    """Invalidate old turns when a capability that could supply their data is revoked."""
    if kind not in ("agents", "tools", "mcps") or not previous["enabled"]:
        return False
    if updated is None or not updated["enabled"]:
        return True
    if kind == "agents":
        if previous.get("allow_uploads", True) and not updated.get("allow_uploads", True):
            return True
        return any(set(previous[key]) - set(updated[key]) for key in ("sources", "tools", "mcps"))
    if kind == "mcps":
        return (
            previous["url"] != updated["url"]
            or previous.get("sealed") != updated.get("sealed")
            or bool(set(previous["tools"]) - set(updated["tools"]))
        )
    return False
