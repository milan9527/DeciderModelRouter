"""Authenticated gateway API with ordered, observable routing and execution events."""

import asyncio
import contextlib
import json
import logging
import secrets
from time import perf_counter
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import Field, model_validator
from starlette.concurrency import run_in_threadpool

from app import routing
from app.gateway_templates import load_templates
from app.routing_inventory import inventory
from app.routing_selector import SELECTOR_VERSION, categories, load_calibration
from app.schemas import StrictModel

router = APIRouter(prefix="/api/gateway", tags=["Model gateway"])
logger = logging.getLogger(__name__)


class GatewayRun(StrictModel):
    task: routing.Task
    provider: Literal["decider", "liquid", "h2o"] | None = None
    mode: Literal["live", "replay"] = "live"
    execute_target: bool = False
    catalog_version: str = Field(max_length=64)
    ranking_version: str = Field(max_length=64)
    policy_version: str = Field(max_length=64)
    session_id: str | None = Field(None, pattern=r"^[a-f0-9]{24}$")
    template_id: str | None = Field(None, max_length=80)
    client_request_id: str | None = Field(None, pattern=r"^[a-zA-Z0-9-]{16,64}$")

    @model_validator(mode="after")
    def replay_cannot_execute(self):
        if self.mode == "replay" and self.execute_target:
            raise ValueError("回放不能执行目标模型")
        return self


@router.get("/catalog")
def catalog(request: Request):
    data, rankings = routing.load_catalog(), routing.load_rankings()
    calibration = load_calibration()
    return {
        "catalog_version": data["version"],
        "ranking_version": rankings["version"],
        "policy_version": SELECTOR_VERSION,
        "calibration_version": calibration["version"],
        "calibration_status": calibration["status"],
        "region": data["region"],
        "captured_at": data.get("captured_at"),
        "models": data["models"],
        "evidence_inventory": inventory(data, rankings),
        "categories": categories(
            data, rankings, routing.Task(text="gateway catalog"), routing.exclusion
        ),
        "examples": load_templates()["items"],
        "templates_version": load_templates()["version"],
        "persistent_history": hasattr(request.app.state, "history"),
        "decision_service": request.app.state.gateway_decision.public_config(),
        "bedrock_configured": request.app.state.bedrock is not None,
    }


@router.get("/status")
async def status(request: Request, provider: Literal["decider", "liquid", "h2o"] | None = None):
    service = request.app.state.gateway_decision
    return {
        "api": "ready",
        "decision": await service.health(provider or service.default_provider),
        "bedrock_configured": request.app.state.bedrock is not None,
    }


async def event_stream(body, request, *, run_id=None, record=None):
    """The response owns the worker: disconnect cancels it, with bounded event buffering."""
    queue = asyncio.Queue(maxsize=16)
    started = perf_counter()
    request_id = run_id or secrets.token_hex(12)
    sequence = 0
    stage = "catalog"
    storage_failed = False
    history = getattr(request.app.state, "history", None)
    user = request.state.user["username"] if history and record else None

    async def send(event_type, data=None, **fields):
        nonlocal sequence, storage_failed
        sequence += 1
        event = {
            "type": event_type,
            "request_id": request_id,
            "sequence": sequence,
            "elapsed_ms": round((perf_counter() - started) * 1000, 3),
            **fields,
            "data": data or {},
        }
        if history and record and not storage_failed:
            try:
                await run_in_threadpool(history.event, user, record, event)
            except Exception as exc:
                sequence -= 1
                storage_failed = True
                logger.exception("History persistence failed: %s", request_id)
                raise HTTPException(
                    503, "历史存储暂时不可用；本次处理已停止，请凭请求 ID 核查。"
                ) from exc
        await queue.put(event)

    async def progress(name, state, data):
        nonlocal stage
        stage = name
        await send("stage", data, stage=name, status=state)

    async def worker():
        nonlocal stage
        try:
            await send(
                "accepted",
                {
                    "mode": body.mode,
                    "provider": body.provider,
                    "execute_target": body.execute_target,
                    "session_id": record["session_id"] if record else None,
                    "history_saved": bool(record),
                },
            )
            route_body = routing.AutoRoute(
                **body.model_dump(
                    exclude={"execute_target", "session_id", "template_id", "client_request_id"}
                )
            )
            route = await routing.run_classified_route(
                route_body,
                request,
                infer=request.app.state.gateway_decision.infer,
                emit=progress,
            )
            await send("route", route)
            executed = None
            if route["status"] == "selected" and body.execute_target:
                stage = "execution"
                await progress(stage, "started", {})
                executed = await routing.auto_execute(
                    routing.AutoExecute(route_token=route["steps"][1]["route_token"]), request
                )
                await progress(stage, "completed", executed)
            await send(
                "completed",
                {
                    "status": ("executed" if executed.get("complete") else "incomplete")
                    if executed
                    else route["status"],
                    "route_ms": route["route_total_ms"],
                    "backend_total_ms": round((perf_counter() - started) * 1000, 3)
                    if body.mode == "live"
                    else None,
                },
            )
        except HTTPException as exc:
            data = {"code": exc.status_code, "message": exc.detail}
            if exc.headers and "Retry-After" in exc.headers:
                data["retry_after_seconds"] = int(exc.headers["Retry-After"])
            await send("error", data, stage=stage)
        except Exception:
            logger.exception("Gateway request failed: %s", request_id)
            await send(
                "error", {"code": 500, "message": "网关处理失败，请凭请求 ID 排查。"}, stage=stage
            )
        finally:
            # Cancellation must not block on a full queue after the reader disconnects.
            if not asyncio.current_task().cancelling():
                await queue.put(None)

    task = asyncio.create_task(worker())
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=5)
            except TimeoutError:
                # Keep proxy connections active without inventing progress or stage timings.
                yield "\n"
                continue
            if event is None:
                break
            yield json.dumps(jsonable_encoder(event), ensure_ascii=False, allow_nan=False) + "\n"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        if history and record and record["status"] == "running":
            with contextlib.suppress(Exception):
                await asyncio.shield(run_in_threadpool(history.interrupted, user, record, stage))


@router.post("/runs")
async def run(body: GatewayRun, request: Request):
    body = body.model_copy(
        update={"provider": body.provider or request.app.state.gateway_decision.default_provider}
    )
    # Reject stale clients before opening the stream or spending inference resources.
    routing.check_version(routing.load_catalog(), body.catalog_version)
    routing.check_ranking_version(routing.load_rankings(), body.ranking_version)
    if body.policy_version != SELECTOR_VERSION:
        raise HTTPException(409, "分类选型策略已更新，请刷新。")
    if body.mode == "live":
        request.app.state.gateway_decision.service(body.provider)
        if body.execute_target and request.app.state.bedrock is None:
            raise HTTPException(503, "Bedrock 尚未配置；可选择仅分析路由。")
    run_id = secrets.token_hex(12)
    record = None
    if history := getattr(request.app.state, "history", None):
        try:
            record = await run_in_threadpool(
                history.begin,
                request.state.user["username"],
                body,
                run_id,
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Cannot create persisted run")
            raise HTTPException(503, "历史数据库不可用，尚未调用模型。") from exc
    return StreamingResponse(
        event_stream(body, request, run_id=run_id, record=record),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
