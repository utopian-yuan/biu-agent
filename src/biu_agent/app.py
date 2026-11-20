"""The embeddable ASGI application. All URLs are relative to its mount."""

import asyncio
import hmac
import json
import secrets
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from agentscope.tool import Toolkit
from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from starlette.datastructures import MutableHeaders

from .actions import ActionHandler, Actions
from .agents.context.attachments import MAX_BODY, MAX_FILES, Upload, decode
from .agent import Settings, run_turn
from .db import Database
from .identity import Principal, Problem, check_origin, csrf_token, install_http_errors
from .session import Sessions, TurnContext, check_session
from .tracing import Tracing


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TurnInput(Empty):
    request_id: UUID
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    attachments: list[Upload] = Field(default_factory=list, max_length=MAX_FILES)
    visible_set_id: UUID | None = None
    model_id: str | None = None


class SessionInput(Empty):
    agent_id: str = ""
    model_id: str = ""


class Rename(Empty):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class Vote(Empty):
    value: Literal["up", "down"] | None


class Revision(Empty):
    request_id: UUID
    model_id: str | None = None


class PageHeaders:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def headers(message):
            if message["type"] == "http.response.start":
                fields = MutableHeaders(scope=message)
                fields["Cache-Control"] = "no-store"
                fields["X-Content-Type-Options"] = "nosniff"
                fields["Content-Security-Policy"] = (
                    "default-src 'self'; frame-ancestors 'self'; object-src 'none'; base-uri 'none'"
                )
                fields["X-Frame-Options"] = "SAMEORIGIN"
            await send(message)

        await self.app(scope, receive, headers)


class TurnStream(Response):
    media_type = "text/event-stream"

    def __init__(self, ctx, request_id, run, sessions, aborted):
        super().__init__(headers={"X-Accel-Buffering": "no"})
        del self.headers["Content-Length"]
        self.ctx = ctx
        self.request_id = request_id
        self.run = run
        self.sessions = sessions
        self.aborted = aborted

    async def __call__(self, scope, receive, send):
        sequence = 0
        lock = asyncio.Lock()

        async def emit(name, payload):
            nonlocal sequence
            async with lock:
                sequence += 1
                data = json.dumps(
                    {**payload, "turn_id": self.ctx.turn_id, "seq": sequence},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                body = f"event: {name}\ndata: {data}\n\n".encode()
                try:
                    await send({"type": "http.response.body", "body": body, "more_body": True})
                except OSError:
                    raise asyncio.CancelledError from None

        async def stream():
            await send({"type": "http.response.start", "status": 200, "headers": self.raw_headers})
            await emit("turn.started", {"request_id": self.request_id})
            await self.run(emit)
            await send({"type": "http.response.body", "body": b"", "more_body": False})

        async def disconnected():
            while (await receive())["type"] != "http.disconnect":
                continue
            self.aborted.set()

        writer = asyncio.create_task(stream())
        watcher = asyncio.create_task(disconnected())
        try:
            done, _ = await asyncio.wait((writer, watcher), return_when=asyncio.FIRST_COMPLETED)
            if writer in done and not writer.cancelled():
                writer.result()
        finally:
            self.aborted.set()
            for task in (writer, watcher):
                task.cancel()
            await asyncio.gather(writer, watcher, return_exceptions=True)
            # Includes a disconnect before the first SSE frame. A committed turn is immutable.
            await self.sessions.fail(self.ctx.turn_id, "interrupted", "interrupted")


def create_app(
    settings: Settings,
    resolve_identity: Callable[[Request], Awaitable[Principal]],
    build_toolkit: Callable[[TurnContext], Toolkit] | None,
    actions: Mapping[str, ActionHandler],
    *,
    workspace=None,
) -> FastAPI:
    if build_toolkit is None and workspace is None:
        raise ValueError("请提供工具装配函数或工作台。")
    sessions = Sessions(Database(settings.data_dir / "core.sqlite3"))
    tracing = Tracing(sessions.db, settings.export_span)
    commands = Actions(sessions, actions, tracing)
    secret = secrets.token_bytes(32)

    @asynccontextmanager
    async def lifespan(_app):
        await sessions.initialize()
        try:
            yield
        finally:
            await commands.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(PageHeaders)
    install_http_errors(app)
    assets = Path(__file__).with_name("web")
    app.mount("/assets", StaticFiles(directory=assets), name="assets")

    async def authenticated(request, *, mutate=False):
        principal = await resolve_identity(request)
        if mutate:
            check_origin(request)
            supplied = request.headers.get("x-csrf-token", "").encode()
            if not hmac.compare_digest(supplied, csrf_token(secret, principal).encode()):
                raise Problem(403, "csrf_denied", "登录状态已变化，请刷新页面。")
        return principal

    @app.get("/chat/")
    async def chat(request: Request):
        try:
            await authenticated(request)
        except Problem as exc:
            if workspace and exc.status == 401:
                return RedirectResponse("/login")
            raise
        return FileResponse(assets / "workspace.html")

    @app.get("/api/bootstrap")
    async def bootstrap(request: Request):
        principal = await authenticated(request)
        return {
            "csrf_token": csrf_token(secret, principal),
            "display_name": principal.display_name,
            "workspace": workspace is not None,
            "model_name": settings.model.model,
        }

    if workspace:
        workspace.install(app, authenticated, sessions)

    @app.get("/api/sessions")
    async def history(
        request: Request,
        q: Annotated[str, Query(max_length=100)] = "",
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        return await sessions.listing(await authenticated(request), q, offset)

    @app.post("/api/sessions", status_code=201)
    async def open_session(request: Request, body: SessionInput):
        principal = await authenticated(request, mutate=True)
        agent_id, model_id = body.agent_id, body.model_id
        if workspace:
            agent_id, model_id = await workspace.selection(principal, agent_id, model_id)
        return {"session_id": await sessions.create(principal, agent_id, model_id)}

    @app.post("/api/sessions/{sid}/rename")
    async def rename(request: Request, sid: UUID, body: Rename):
        await sessions.manage(await authenticated(request, mutate=True), str(sid), title=body.title)
        return {"title": body.title}

    @app.post("/api/sessions/{sid}/delete")
    async def remove(request: Request, sid: UUID, body: Empty):
        await sessions.manage(await authenticated(request, mutate=True), str(sid), delete=True)
        return {"deleted": True}

    @app.post("/api/sessions/{sid}/turns/{tid}/feedback")
    async def feedback(request: Request, sid: UUID, tid: UUID, body: Vote):
        await sessions.feedback(await authenticated(request, mutate=True), str(sid), str(tid), body.value)
        return {"value": body.value}

    @app.get("/api/sessions/{sid}")
    async def snapshot(request: Request, sid: UUID, before: Annotated[int | None, Query(gt=0)] = None):
        principal = await authenticated(request)
        data = await sessions.snapshot(principal, str(sid), before)
        await commands.receipts(principal, data)
        principal.check(await resolve_identity(request), data["agent_id"])
        return data

    @app.post("/api/sessions/{sid}/turns")
    async def turn(request: Request, sid: UUID):
        principal = await authenticated(request, mutate=True)
        # JSON carries bounded base64 so all the attachments and the question commit together.
        payload = bytearray()
        async for chunk in request.stream():
            if len(payload) + len(chunk) > MAX_BODY:
                raise Problem(413, "upload_limit", "每次最多 4 个文件，每个最大 2 MiB。")
            payload.extend(chunk)
        try:
            body = TurnInput.model_validate_json(payload)
        except ValidationError:
            raise Problem(
                422, "invalid_input", "请检查问题和附件。每次最多 4 个文件，每个最大 2 MiB。"
            ) from None
        docs = []
        if body.attachments:
            if not workspace:
                raise Problem(403, "uploads_disabled", "当前宿主没有启用附件。")
            aid = await sessions.db.read(lambda db: check_session(db, str(sid), principal)["agent_id"])
            await workspace.check_uploads(principal, aid)
            docs = await asyncio.to_thread(lambda: [decode(f) for f in body.attachments])
            principal.check(await resolve_identity(request), aid)
        return await respond(
            request, principal, sid, body.request_id, body.text, body.visible_set_id, body.model_id, docs=docs
        )

    @app.post("/api/sessions/{sid}/turns/{tid}/regenerate")
    async def regenerate(request: Request, sid: UUID, tid: UUID, body: Revision):
        principal = await authenticated(request, mutate=True)
        return await respond(
            request, principal, sid, body.request_id, "", None, body.model_id, source=str(tid)
        )

    async def respond(request, principal, sid, rid, text, visible_id, model_id, *, source=None, docs=()):
        request_id = str(rid)
        if workspace and model_id:
            await workspace.validate_model(principal, model_id)
        visible = str(visible_id) if visible_id else None
        ctx = await sessions.start(
            principal, str(sid), request_id, text, visible, source=source, model_id=model_id, docs=docs
        )
        if isinstance(ctx, dict):
            return JSONResponse(ctx)

        aborted = asyncio.Event()

        async def revalidate():
            if aborted.is_set():
                raise asyncio.CancelledError
            principal.check(await resolve_identity(request), ctx.agent_id)

        async def run(emit):
            await run_turn(
                settings, sessions, tracing, ctx, build_toolkit, revalidate, emit, workspace=workspace
            )

        return TurnStream(ctx, request_id, run, sessions, aborted)

    @app.post("/api/sessions/{sid}/actions/{aid}/confirm")
    async def confirm(request: Request, sid: UUID, aid: UUID, body: Empty):
        principal = await authenticated(request, mutate=True)

        async def revalidate():
            principal.check(await resolve_identity(request))

        status, data = await commands.confirm(principal, str(sid), str(aid), revalidate)
        return JSONResponse(data, status_code=status)

    @app.post("/api/sessions/{sid}/actions/{aid}/cancel")
    async def cancel(request: Request, sid: UUID, aid: UUID, body: Empty):
        principal = await authenticated(request, mutate=True)
        status, data = await commands.cancel(principal, str(sid), str(aid))
        return JSONResponse(data, status_code=status)

    return app
