"""Local registration, explicit sign-in and revocable account-bound browser sessions."""

import asyncio
import hashlib
import hmac
import os
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from biu_agent.db import Database, new_id, now_ms
from biu_agent.identity import Principal, Problem, check_origin

from .config import seed_account

COOKIE = "biu_workspace"
SCHEMA = """
CREATE TABLE accounts(id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
 salt BLOB NOT NULL, password_hash BLOB NOT NULL, epoch INTEGER NOT NULL DEFAULT 1, guided INTEGER NOT NULL DEFAULT 0);
CREATE TABLE logins(digest TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES accounts(id), expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS deployment_policy(id INTEGER PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE resources(user_id TEXT NOT NULL REFERENCES accounts(id), kind TEXT NOT NULL, id TEXT NOT NULL,
 data TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(user_id,kind,id));
CREATE TABLE agent_epochs(user_id TEXT NOT NULL REFERENCES accounts(id), id TEXT NOT NULL, revision INTEGER NOT NULL,
 PRIMARY KEY(user_id,id))
"""

# Older installations had one owner. Keep that account's ID, sessions, resources and access epochs together.
MIGRATE_OWNER = (
    """
ALTER TABLE logins RENAME TO previous_logins;
ALTER TABLE resources RENAME TO previous_resources;
ALTER TABLE agent_epochs RENAME TO previous_epochs;
"""
    + SCHEMA
    + """;
INSERT INTO accounts SELECT id,name,salt,password_hash,epoch,guided FROM owner;
INSERT INTO logins SELECT l.digest,o.id,l.expires FROM previous_logins l CROSS JOIN owner o;
INSERT INTO resources SELECT o.id,r.kind,r.id,r.data,r.revision FROM previous_resources r CROSS JOIN owner o;
INSERT INTO agent_epochs SELECT o.id,e.id,e.revision FROM previous_epochs e CROSS JOIN owner o;
DROP TABLE previous_logins;
DROP TABLE previous_resources;
DROP TABLE previous_epochs;
DROP TABLE owner
"""
)


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=48)]
    password: str = Field(min_length=8, max_length=128, repr=False)


def hash_password(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Accounts:
    def __init__(self, directory, model_name=""):
        self.db = Database(Path(directory) / "workspace.sqlite3")
        self.model_name = model_name
        self.failures = {}
        self.login_lock = asyncio.Lock()
        self.dummy_salt = secrets.token_bytes(16)

    async def initialize(self):
        await self.db.initialize(
            SCHEMA,
            migrations=(
                "CREATE TABLE agent_epochs(id TEXT PRIMARY KEY, revision INTEGER NOT NULL)",
                MIGRATE_OWNER,
            ),
            current_schema=True,
        )
        os.chmod(self.db.path, 0o600)

    async def identity(self, request):
        token = request.cookies.get(COOKIE, "")

        def resolve(db):
            row = db.execute(
                """SELECT a.id,a.name,a.epoch FROM logins l JOIN accounts a ON a.id=l.user_id
                WHERE l.digest=? AND l.expires>?""",
                (digest(token), now_ms()),
            ).fetchone()
            if not row:
                raise Problem(401, "login_required", "请登录材料工作台。")
            epoch = str(row["epoch"])
            versions = {
                key: f"{epoch}.{rev}"
                for key, rev in db.execute(
                    "SELECT id,revision FROM agent_epochs WHERE user_id=?", (row["id"],)
                )
            }
            return Principal("local", row["id"], epoch, token, row["name"], versions)

        return await self.db.read(resolve)

    async def register(self, credentials, previous_token=None):
        salt = secrets.token_bytes(16)
        async with self.login_lock:
            hashed = await asyncio.to_thread(hash_password, credentials.password, salt)
        user_id = new_id()

        def create(db):
            inserted = db.execute(
                """INSERT INTO accounts(id,name,salt,password_hash) VALUES(?,?,?,?)
                ON CONFLICT(name) DO NOTHING""",
                (user_id, credentials.username, salt, hashed),
            )
            if not inserted.rowcount:
                raise Problem(409, "name_taken", "这个用户名已经注册，请登录或换一个用户名。")
            seed_account(db, user_id, self.model_name)
            if previous_token:
                db.execute("DELETE FROM logins WHERE digest=?", (digest(previous_token),))

        await self.db.write(create)

    async def login(self, credentials):
        async with self.login_lock:
            when = now_ms()
            key = credentials.username.casefold()
            self.failures = {
                name: [t for t in attempts if t > when - 60_000]
                for name, attempts in self.failures.items()
                if attempts[-1] > when - 60_000
            }
            if len(self.failures.get(key, ())) >= 10:
                raise Problem(429, "login_limited", "尝试次数较多，请一分钟后再试。")
            row = await self.db.read(
                lambda db: db.execute(
                    "SELECT id,salt,password_hash FROM accounts WHERE name=?", (credentials.username,)
                ).fetchone()
            )
            hashed = await asyncio.to_thread(
                hash_password, credentials.password, row["salt"] if row else self.dummy_salt
            )
            if not row or not hmac.compare_digest(hashed, row["password_hash"]):
                self.failures.setdefault(key, []).append(when)
                raise Problem(401, "bad_login", "用户名或密码不正确。")
            token = secrets.token_urlsafe(32)

            def issue(db):
                db.execute("DELETE FROM logins WHERE expires<=?", (when,))
                db.execute(
                    "INSERT INTO logins(digest,user_id,expires) VALUES(?,?,?)",
                    (digest(token), row["id"], when + 86400_000),
                )

            await self.db.write(issue)
            self.failures.pop(key, None)
            return token

    def routes(self, app, assets):
        @app.get("/login")
        @app.get("/register")
        async def account_page():
            return FileResponse(assets / "login.html")

        @app.post("/auth/register", status_code=201)
        async def register(request: Request, body: Credentials):
            check_origin(request)
            previous_token = request.cookies.get(COOKIE)
            await self.register(body, previous_token)
            response = JSONResponse({"registered": True}, status_code=201)
            if previous_token:
                response.delete_cookie(COOKIE)
            return response

        @app.post("/auth/login")
        async def authenticate(request: Request, body: Credentials):
            check_origin(request)
            token = await self.login(body)
            res = JSONResponse({"ok": True})
            res.set_cookie(
                COOKIE,
                token,
                httponly=True,
                secure=request.url.scheme == "https",
                samesite="strict",
                max_age=86400,
            )
            return res

        @app.post("/auth/logout")
        async def logout(request: Request):
            check_origin(request)
            await self.db.write(
                lambda db: db.execute(
                    "DELETE FROM logins WHERE digest=?", (digest(request.cookies.get(COOKIE, "")),)
                )
            )
            res = JSONResponse({"ok": True})
            res.delete_cookie(COOKIE)
            return res

        @app.get("/")
        @app.get("/chat")
        @app.get("/demo/")
        async def home(request: Request):
            try:
                await self.identity(request)
            except Problem as exc:
                if exc.status != 401:
                    raise
                return RedirectResponse("/login")
            return RedirectResponse("/assistant/chat/")
