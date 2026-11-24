"""Independent DeciderModelRouter service: no legacy application or GPU lifecycle API."""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.auth import Auth
from app.auth import router as auth_router
from app.bedrock import TARGET_READ_TIMEOUT_SECONDS, create_client
from app.gateway import router as gateway_router
from app.gateway_accounts import router as accounts_router
from app.gateway_decision import GatewayDecisionService
from app.gateway_evaluations import Evaluations
from app.gateway_evaluations import router as evaluations_router
from app.gateway_history import router as history_router
from app.gateway_store import History


@asynccontextmanager
async def lifespan(app):
    app.state.auth = Auth()
    app.state.history = History()
    app.state.bedrock = create_client()
    app.state.bedrock_target = create_client(read_timeout=TARGET_READ_TIMEOUT_SECONDS)
    app.state.bedrock_slots = asyncio.Semaphore(2)
    async with httpx.AsyncClient(trust_env=False) as client:
        app.state.client = client
        app.state.gateway_decision = GatewayDecisionService(client)
        app.state.evaluations = Evaluations(app.state.history, app.state.gateway_decision)
        try:
            yield
        finally:
            await app.state.evaluations.close()
            if app.state.bedrock:
                app.state.bedrock.close()
            if app.state.bedrock_target:
                app.state.bedrock_target.close()


app = FastAPI(
    title="DeciderModelRouter",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
)
for router in (auth_router, gateway_router, history_router, evaluations_router, accounts_router):
    app.include_router(router)


@app.middleware("http")
async def authenticate(request, call_next):
    return await request.app.state.auth.middleware(request, call_next)


@app.get("/api/live", include_in_schema=False)
def live():
    return {"status": "ok", "service": "DeciderModelRouter"}


@app.get("/api/ready", include_in_schema=False)
def ready():
    # Upstream outages do not take login, saved history or diagnostics offline.
    return {"status": "ready", "service": "DeciderModelRouter"}


if os.getenv("SERVE_FRONTEND", "false").lower() == "true":
    frontend = Path(__file__).resolve().parent.parent / "frontend/gateway"
    app.mount("/gateway", StaticFiles(directory=frontend, html=True), name="gateway")

    @app.get("/", include_in_schema=False)
    def index():
        return RedirectResponse("/gateway/index.html")
