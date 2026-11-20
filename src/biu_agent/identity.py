"""The host authenticates requests. The core binds each session to that scope."""

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


class Problem(Exception):
    def __init__(self, status: int, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable

    def detail(self):
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


def hidden() -> Problem:
    return Problem(404, "not_found", "记录不存在或当前账号不可见。")


def scope_changed() -> Problem:
    return Problem(409, "scope_changed", "权限已变化，请新建会话。")


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    user_id: str
    access_epoch: str
    credential: str = field(repr=False)
    display_name: str = ""
    agent_epochs: dict[str, str] = field(default_factory=dict, repr=False, compare=False)

    @property
    def scope(self) -> tuple[str, str, str]:
        return self.tenant_id, self.user_id, self.access_epoch

    def epoch_for(self, agent_id):
        return self.agent_epochs.get(agent_id, self.access_epoch)

    def check(self, other: "Principal", agent_id=""):
        if self.scope[:2] != other.scope[:2]:
            raise hidden()
        if self.epoch_for(agent_id) != other.epoch_for(agent_id):
            raise scope_changed()


def check_origin(request: Request):
    if request.headers.get("origin") != f"{request.url.scheme}://{request.url.netloc}":
        raise Problem(403, "origin_denied", "请在宿主系统的同源页面操作。")


def csrf_token(secret: bytes, principal: Principal) -> str:
    material = json.dumps([principal.tenant_id, principal.user_id, principal.credential]).encode()
    return hmac.new(secret, material, hashlib.sha256).hexdigest()


def install_http_errors(app: FastAPI):
    def response(error: Problem):
        return JSONResponse(
            {"error": error.detail(), "request_id": str(uuid4())},
            status_code=error.status,
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(Problem)
    async def problem_response(_request, exc):
        return response(exc)

    @app.exception_handler(RequestValidationError)
    async def validation_response(_request, _exc):
        return response(Problem(422, "invalid_input", "参数格式或长度无效。"))

    @app.exception_handler(HTTPException)
    async def http_response(_request, exc):
        return response(Problem(exc.status_code, "http_error", "请求的资源或方法不可用。"))
