"""Bind the active model, source grants and read-only tools to a single conversation turn."""

import json
import re
from dataclasses import replace
from uuid import UUID
from urllib.parse import quote

from agentscope.credential import OpenAICredential
from agentscope.model import OpenAIChatModel
from agentscope.tool import Toolkit
from fastapi import Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from biu_agent.agents.context.attachments import files_through
from biu_agent.agents.mcp import discover
from biu_agent.agents.md_files import materials_prompt
from biu_agent.agents.skills import bind, bundled, export, unpack
from biu_agent.agents.tools import material_tools
from biu_agent.agents.tools.conversation import ConversationTools
from biu_agent.identity import Problem, hidden
from biu_agent.session import check_session
from biu_agent.tools import Inputs, tool

from .config import Configuration, account_record
from .corpus import FILES, Passage, catalog, document, excerpt
from .teaching_model import TeachingModel


class Edit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict = Field(default_factory=dict, max_length=12)
    revision: int = Field(default=0, ge=0)


class SkillImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    markdown: str = Field(min_length=1, max_length=12000)


class Workspace:
    def __init__(self, accounts, model=None, grants=None):
        self.config = Configuration(accounts, model)
        self.grants = set(FILES if grants is None else grants)
        if not self.grants <= FILES.keys():
            raise ValueError("材料授权清单含未登记的文件。")
        self.teaching = TeachingModel()

    async def initialize(self):
        await self.config.initialize()
        boundary = ",".join(sorted(self.grants))

        def sync(db):
            last = db.execute("SELECT value FROM deployment_policy WHERE id=1").fetchone()
            if last and last[0] != boundary:
                db.execute("UPDATE accounts SET epoch=epoch+1")
            db.execute("INSERT OR REPLACE INTO deployment_policy VALUES(1,?)", (boundary,))

        await self.config.db.write(sync)

    @staticmethod
    def active(rows, key):
        item = rows.get(key)
        if not item or not item["enabled"]:
            raise Problem(409, "unavailable", "所选能力已停用或移除，请重新选择。")
        return item

    def model(self, row):
        origin = row.get("origin")
        if origin == "teaching":
            return self.teaching
        if origin == "environment":
            return self.config.real_model
        return OpenAIChatModel(
            credential=OpenAICredential(api_key=self.config.token(row) or "local", base_url=row["url"]),
            model=row["model"],
            stream=True,
            max_retries=0,
        )

    async def selection(self, p, agent_id, model_id):
        conf = await self.config.get(p)
        agent = self.active(conf["agents"], agent_id or "materials")
        preferred = model_id or agent["model_id"]
        if not model_id and (preferred not in conf["models"] or not conf["models"][preferred]["enabled"]):
            preferred = "teaching"
        return agent["id"], self.active(conf["models"], preferred)["id"]

    async def validate_model(self, p, key):
        self.active((await self.config.get(p))["models"], key)

    def sources(self, agent):
        return self.grants.intersection(agent["sources"])

    async def check_uploads(self, p, agent_id):
        cfg = await self.config.get(p)
        agent = self.active(cfg["agents"], agent_id)
        if not agent.get("allow_uploads", True):
            raise Problem(403, "uploads_disabled", "此智能体已关闭对话附件。")

    async def prepare(self, ctx, settings):
        cfg = await self.config.get(ctx.principal)
        agent = self.active(cfg["agents"], ctx.agent_id)
        model = self.model(self.active(cfg["models"], ctx.model_id))
        allowed = self.sources(agent)
        ctx.profile["sources"] = allowed

        uploads = ctx.files if agent.get("allow_uploads", True) else {}
        focused = ctx.source_turn or ctx.turn_id

        def documents(id=None):
            keys = [id] if id else [*uploads, *(k for k in FILES if k in allowed)]
            return (document(k, allowed, uploads) for k in keys)

        async def list_materials():
            result = []
            for doc in documents():
                sections = list(doc["sections"])
                row = {"id": doc["id"], "title": doc["title"]}
                if doc["id"] in uploads:
                    # Uploaded sections always form the complete contiguous range S1..Sn.
                    row.update(
                        attachment=True,
                        current=doc["turn_id"] == focused,
                        sections=[sections[0]],
                        last_section=sections[-1],
                        count=len(sections),
                    )
                else:
                    row["sections"] = sections
                result.append(row)
            return result

        def read(doc, section):
            return excerpt(doc, section, allowed, uploads)

        async def read_material(id, section):
            return read(id, section)

        def record_excerpt(row):
            ctx.evidence[row["source"]] = {k: row[k] for k in ("source", "id", "section", "title", "sha256")}

        available = [
            tool(
                list_materials,
                Inputs,
                "列出授权资料与附件。附件段号从 S1 连续至 last_section，current 表示原问题所附文件。",
            ),
            tool(read_material, Passage, "读取指定文件的一段原文。可并行取证。", on_result=record_excerpt),
        ]
        selected = {k for k in agent["tools"] if cfg["tools"][k]["enabled"]}
        if "read_material" in selected:
            available.extend(material_tools(documents, read, record_excerpt))
        scope = ConversationTools(
            ctx, documents if "read_material" in selected else lambda: (), record_excerpt
        )
        available.extend(scope.build())
        if ctx.source_turn:
            selected -= {"write", "edit"}
        tools = [t for t in available if t.name in selected]
        skills = bind(cfg["skills"], agent["skills"])
        for key in agent["mcps"]:
            row = cfg["mcps"][key]
            if row["enabled"] and row["tools"]:
                client, safe = await discover(row, self.config.token(row))
                tools.extend([await client.get_tool(n) for n in safe if n in row["tools"]])
        ctx.context_summary = {
            "history_questions": len(ctx.history),
            "sources": [FILES[key] for key in FILES if key in allowed]
            + [d["title"] for d in uploads.values()],
            "tools": [item.name for item in tools],
            "skills": [
                cfg["skills"][key]["name"] for key in agent["skills"] if cfg["skills"][key]["enabled"]
            ],
        }
        prompt = materials_prompt() + "\n" + agent["prompt"]
        if uploads:
            focused_ids = [d["id"] for d in uploads.values() if d["turn_id"] == focused]
            prompt += (
                "\n本题新附文件 ID："
                + json.dumps(focused_ids)
                + "。通过目录查看文件名和段号，再用工具读取正文。"
            )
        return replace(settings, model=model, system_prompt=prompt), Toolkit(
            tools=tools,
            skills_or_loaders=skills,
            skill_instruction_template="{% for skill in skills %}需要{{ skill.description }}时，使用 {{ skill_viewer }} 读取 {{ skill.name }}。{% endfor %}",
        )

    def verify(self, ctx, answer):
        refs = set(re.findall(r"\[([A-Za-z][A-Za-z0-9_-]*#[A-Za-z][A-Za-z0-9_-]*)\]", answer))
        if refs - ctx.evidence.keys():
            raise Problem(409, "unverified_source", "回答包含本轮未读取的出处，请重新生成。")
        for row in ctx.evidence.values():
            current = ctx.work.passages.get(row["source"]) or excerpt(
                row["id"], row["section"], ctx.profile["sources"], ctx.files
            )
            if current["sha256"] != row["sha256"]:
                raise Problem(409, "source_changed", "原文已更新，请重新核对。")
        if ctx.evidence and not refs:
            raise Problem(409, "missing_citation", "模型读取了材料，但答复没有给出可核对的段号，请重新生成。")
        supplied = any(f["turn_id"] == (ctx.source_turn or ctx.turn_id) for f in ctx.files.values())
        if (
            not ctx.evidence
            and not ctx.work.changes
            and (supplied or re.search(r"附件|到货|验收|签收|采购|样品灯|导向牌", ctx.input_text))
        ):
            return "本轮没有读取到可核对的段落，现有依据不足。请检查当前智能体的资料范围与读取工具。"
        return answer

    def install(self, app, auth, sessions):
        @app.get("/api/workspace")
        async def settings(request: Request):
            result = await self.config.get(await auth(request), public=True)
            result["source_names"] = {k: v for k, v in FILES.items() if k in self.grants}
            return result

        @app.post("/api/workspace/guide")
        async def guide(request: Request):
            p = await auth(request, mutate=True)

            def mark(db):
                account_record(db, p)
                db.execute("UPDATE accounts SET guided=1 WHERE id=?", (p.user_id,))

            await self.config.db.write(mark)
            return {"guided": True}

        @app.get("/api/skills/library")
        async def skill_library(request: Request):
            cfg = await self.config.get(await auth(request))
            items = []
            for slug, defaults in bundled().items():
                current = cfg["skills"].get(slug, {})
                matches = all(current.get(k) == defaults[k] for k in ("name", "description", "content"))
                items.append(
                    {
                        "id": slug,
                        "name": defaults["name"],
                        "description": defaults["description"],
                        "revision": current.get("revision", 0),
                        "matches": matches,
                    }
                )
            return {"items": items}

        @app.post("/api/skills/library/{key}")
        async def install_bundled(request: Request, key: str, body: Edit):
            p = await auth(request, mutate=True)
            data = bundled().get(key)
            if not data:
                raise hidden()
            if not body.revision:
                return await self.config.save(p, "skills", None, data, 0, import_id=key)
            current = (await self.config.get(p))["skills"].get(key)
            if not current:
                raise hidden()
            data["enabled"] = current["enabled"]
            return await self.config.save(p, "skills", key, data, body.revision)

        @app.post("/api/skills/import", status_code=201)
        async def import_skill(request: Request, body: SkillImport):
            p = await auth(request, mutate=True)
            slug, values = unpack(body.markdown)
            return await self.config.save(p, "skills", None, values, 0, import_id=slug)

        @app.get("/api/skills/{key}/export")
        async def export_skill(request: Request, key: str):
            cfg = await self.config.get(await auth(request))
            row = cfg["skills"].get(key)
            if not row:
                raise hidden()
            return PlainTextResponse(
                export(row),
                media_type="text/markdown",
                headers={"Content-Disposition": 'attachment; filename="SKILL.md"'},
            )

        @app.post("/api/workspace/{kind}/add")
        async def add(kind: str, request: Request, body: Edit):
            return await self.config.save(await auth(request, mutate=True), kind, None, body.values, 0)

        @app.post("/api/workspace/{kind}/{key}/save")
        async def update(kind: str, key: str, request: Request, body: Edit):
            return await self.config.save(
                await auth(request, mutate=True), kind, key, body.values, body.revision
            )

        @app.post("/api/workspace/{kind}/{key}/delete")
        async def remove(kind: str, key: str, request: Request, body: Edit):
            return await self.config.save(
                await auth(request, mutate=True), kind, key, {}, body.revision, delete=True
            )

        @app.post("/api/connections/{key}/test")
        async def test_mcp(key: str, request: Request):
            cfg = await self.config.get(await auth(request, mutate=True))
            row = cfg["mcps"].get(key)
            if not row:
                raise hidden()
            _, names = await discover(row, self.config.token(row))
            return {"tools": names}

        @app.get("/api/materials")
        async def materials(request: Request, agent_id: str = "materials"):
            cfg = await self.config.get(await auth(request))
            return {"items": catalog(self.sources(self.active(cfg["agents"], agent_id)))}

        @app.get("/api/materials/{doc}/{section}")
        async def read(request: Request, doc: str, section: str, agent_id: str = "materials"):
            cfg = await self.config.get(await auth(request))
            return excerpt(doc, section, self.sources(self.active(cfg["agents"], agent_id)))

        @app.get("/api/sessions/{sid}/turns/{tid}/file")
        async def download(request: Request, sid: UUID, tid: UUID, path: str):
            p = await auth(request)

            def lookup(db):
                session = check_session(db, str(sid), p)
                row = db.execute(
                    "SELECT body FROM work_versions WHERE session_id=? AND turn_id=? AND path=?",
                    (str(sid), str(tid), path),
                ).fetchone()
                if not row:
                    raise hidden()
                return session["agent_id"], row["body"]

            aid, body = await sessions.db.read(lookup)
            p.check(await auth(request), aid)
            name = quote(path.rsplit("/", 1)[-1], safe="")
            return Response(
                body.encode("utf-8"),
                media_type="application/octet-stream",
                headers={"Content-Disposition": "attachment; filename*=UTF-8''" + name},
            )

        @app.get("/api/sessions/{sid}/turns/{tid}/sources/{doc}/{section}")
        async def source(request: Request, sid: UUID, tid: UUID, doc: str, section: str):
            p = await auth(request)

            def lookup(db):
                session = check_session(db, str(sid), p)
                row = db.execute(
                    """SELECT v.evidence_json,q.ordinal FROM turns v JOIN turns q ON q.id=coalesce(v.source_turn,v.id)
                       WHERE v.session_id=? AND v.id=?""",
                    (str(sid), str(tid)),
                ).fetchone()
                if not row:
                    raise hidden()
                ref = next((r for r in json.loads(row[0]) if (r["id"], r["section"]) == (doc, section)), None)
                if not ref:
                    raise hidden()
                files = files_through(db, str(sid), row["ordinal"]) if doc.startswith("file_") else {}
                saved = db.execute(
                    "SELECT data FROM tool_passages WHERE turn_id=? AND source=?", (str(tid), ref["source"])
                ).fetchone()
                return session["agent_id"], ref, files, json.loads(saved["data"]) if saved else None

            aid, ref, files, saved = await sessions.db.read(lookup)
            cfg = await self.config.get(p)
            agent = self.active(cfg["agents"], aid)
            original = saved or excerpt(
                doc, section, self.sources(agent), files if agent.get("allow_uploads", True) else {}
            )
            if original["sha256"] != ref["sha256"]:
                raise Problem(409, "source_changed", "材料已更新，请重新取证。")
            p.check(await auth(request), aid)
            return original
