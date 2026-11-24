"""Private gateway for two independently resident GPU model services."""

import asyncio
import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

MODEL_IDS = {"liquid": "LiquidAI/d1-2B", "h2o": "h2oai/h2o-lightning-4b"}
ENDPOINTS = {"liquid": "http://127.0.0.1:18801", "h2o": "http://127.0.0.1:18803"}


def validate(payload):
    if not isinstance(payload, dict) or set(payload) != {"state", "questions"}:
        raise HTTPException(422, "Expected state and questions")
    if not isinstance(payload["state"], str) or len(payload["state"]) > 24000:
        raise HTTPException(422, "Invalid state")
    questions = payload["questions"]
    if not isinstance(questions, dict) or not 1 <= len(questions) <= 16:
        raise HTTPException(422, "Invalid question count")
    for q in questions.values():
        if not isinstance(q, dict) or q.get("type") != "choice" or not isinstance(q.get("criteria"), dict):
            raise HTTPException(422, "Only typed choice questions are supported")
        if not 2 <= len(q["criteria"]) <= 32:
            raise HTTPException(422, "Invalid candidate count")


@asynccontextmanager
async def lifespan(app):
    app.state.client = httpx.AsyncClient(trust_env=False)
    app.state.slots = {k: asyncio.Semaphore(1) for k in MODEL_IDS}
    try:
        yield
    finally:
        await app.state.client.aclose()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def authenticate(request, call_next):
    key_file = os.getenv("OPEN_MODELS_API_KEY_FILE")
    if key_file:
        try:
            key = Path(key_file).read_text().strip()
        except OSError:
            key = ""
        if not key:
            return JSONResponse({"detail": "Service authentication unavailable"}, status_code=503)
    else:
        key = os.getenv("OPEN_MODELS_API_KEY", "")
    if key and not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + key):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return await call_next(request)


@app.get("/health")
async def health(request: Request):
    result = {}
    for provider, url in ENDPOINTS.items():
        try:
            response = await request.app.state.client.get(url + "/health", timeout=2)
            result[provider] = response.status_code == 200 and response.json().get("model") == MODEL_IDS[provider]
        except (httpx.HTTPError, ValueError):
            result[provider] = False
    return {"status": "ok" if all(result.values()) else "starting", "resident": True, "models": result,
            "per_model_concurrency": 1, "instance": os.getenv("RESIDENT_INSTANCE_ID", "")}


@app.post("/v1/systemone/{provider}")
async def decide(provider: str, payload: dict, request: Request):
    if provider not in MODEL_IDS:
        raise HTTPException(404, "Unknown model")
    validate(payload)
    try:
        async with asyncio.timeout(70):
            async with request.app.state.slots[provider]:
                response = await request.app.state.client.post(ENDPOINTS[provider] + "/v1/systemone",
                                                               json=payload, timeout=65)
                response.raise_for_status()
                data = response.json()
        if data.get("model") != MODEL_IDS[provider]:
            raise ValueError("Unexpected model identity")
        data["deployment"] = "FP8 · vLLM 0.30.0 · resident" if provider == "h2o" else "BF16 · Transformers · resident"
        return data
    except (httpx.HTTPError, TimeoutError, ValueError) as exc:
        raise HTTPException(503, "常驻模型尚未就绪或本次调用失败；没有切换模型") from exc


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=18124, access_log=False)
