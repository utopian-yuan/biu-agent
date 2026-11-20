"""Start the standalone Chinese assistant with the tutorial's three source files."""

import argparse
import os
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from starlette.middleware.trustedhost import TrustedHostMiddleware

from biu_agent import Settings, create_app
from biu_agent.app import PageHeaders
from biu_agent.identity import install_http_errors

from .accounts import Accounts
from .teaching_model import TeachingModel
from .workspace import Workspace


def create_materials(settings, *, grants=None):
    real = None if isinstance(settings.model, TeachingModel) else settings.model
    accounts = Accounts(settings.data_dir, real.model if real else "")
    workspace = Workspace(accounts, real, grants)
    assistant = create_app(settings, accounts.identity, None, {}, workspace=workspace)

    @asynccontextmanager
    async def lifespan(_app):
        await workspace.initialize()
        async with assistant.router.lifespan_context(assistant):
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])
    app.add_middleware(PageHeaders)
    install_http_errors(app)
    accounts.routes(app, files("biu_agent") / "web")
    app.mount("/assistant", assistant)
    return app


def main():
    parser = argparse.ArgumentParser(description="用教程材料启动中文助手工作台")
    parser.add_argument("--scripted", action="store_true", help="使用可离线运行的材料教学模型")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--langfuse", action="store_true")
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    directory = Path(os.getenv("BIU_DATA_DIR") or ".biu") / "workspace"
    settings = Settings(directory, TeachingModel())
    if not args.scripted and os.getenv("BIU_MODEL_NAME") and os.getenv("BIU_MODEL_API_KEY"):
        settings = Settings.from_env()
        settings.data_dir = directory
    grants = os.getenv("BIU_MATERIAL_SOURCES")
    allowed = None if grants is None else [v.strip() for v in grants.split(",") if v.strip()]
    if args.langfuse:
        from integrations.langfuse import LangfuseExport

        settings.export_span = LangfuseExport.from_env()
    print(f"材料工作台 http://127.0.0.1:{args.port}/")
    try:
        uvicorn.run(
            create_materials(settings, grants=allowed), host="127.0.0.1", port=args.port, access_log=False
        )
    finally:
        if args.langfuse:
            settings.export_span.close()


if __name__ == "__main__":
    main()
