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
from app.gateway_conversation import Conversation, Message, SessionOptions, validate_messages
from app.gateway_templates import load_templates
from app.routing_inventory import inventory
from app.routing_media import capability_catalog
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
    session: SessionOptions = Field(default_factory=SessionOptions)
    history: list[Message] | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def replay_cannot_execute(self):
        if self.mode == "live" and "include_legacy" not in self.task.model_fields_set:
            self.task = self.task.model_copy(update={"include_legacy": True})
        if self.mode == "replay" and self.execute_target:
            raise ValueError("回放不能执行目标模型")
        if self.history is not None:
            validate_messages(self.history)
            if self.session.use_history:
                raise ValueError("显式 history 与自动会话历史不能同时启用，请设置 session.use_history=false")
        return self


@router.get("/catalog")
def catalog(request: Request):
    from app.bedrock_evidence import load_evidence, measured
    from app.gateway_model_pool import MODEL_ROUTING_VERSION, build_pool
    from app.public_benchmarks import load_public_benchmarks
    from app.routing_profile import replay_profile
    data, rankings = routing.load_catalog(), routing.load_rankings()
    evidence = load_evidence()
    for model in data["models"]:
        model["routing_capabilities"] = {**model.get("routing_capabilities", {}),
                                          **measured(model, evidence).get("capabilities", {})}
    calibration = load_calibration()
    media = capability_catalog(data, routing.exclusion)
    grouped = categories(data, rankings, routing.Task(text="gateway catalog"), routing.exclusion)
    for workflow, group in grouped.items():
        task = routing.Task(text="gateway catalog")
        pool = build_pool(task, replay_profile(task, workflow), data, rankings, routing.exclusion)
        ids = sorted({r["model"]["id"] for r in pool["candidates"]})
        group.update(available_model_ids=ids, available_model_count=len(ids),
                     configuration_count=len(pool["candidates"]))
    for workflow in {s["workflow"] for s in media["scenarios"]}:
        ids = sorted({mid for s in media["scenarios"] if s["workflow"] == workflow for mid in s["model_ids"]})
        grouped[workflow].update(
            available_model_ids=ids, available_model_count=len(ids),
            configuration_count=len(ids), evidence_mode="capability_only",
        )
    return {
        "catalog_version": data["version"],
        "ranking_version": rankings["version"],
        "policy_version": SELECTOR_VERSION,
        "calibration_version": calibration["version"],
        "calibration_status": calibration["status"],
        "model_routing": {"version": MODEL_ROUTING_VERSION, "selector": "gpu_decision_model",
                          "measurement_version": load_evidence()["version"],
                          "public_benchmark_version": load_public_benchmarks()["version"],
                          "benchmark_cohort_exclusion": False},
        "region": data["region"],
        "captured_at": data.get("captured_at"),
        "models": data["models"],
        "evidence_inventory": inventory(data, rankings),
        "categories": grouped,
        "examples": load_templates()["items"],
        "templates_version": load_templates()["version"],
        "persistent_history": hasattr(request.app.state, "history"),
        "session_features": {
            "multi_turn": True, "stickiness": True, "prompt_cache": "bedrock_native",
            "streaming": "converse_stream", "fallback_max_attempts": 3,
            "real_time_search": False,
        } if hasattr(request.app.state, "history") else None,
        "decision_service": request.app.state.gateway_decision.public_config(),
        "media": media,
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
    runtime = getattr(getattr(request, "state", None), "gateway_runtime", None)

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
        if runtime and (observer := getattr(runtime, "route_progress", None)):
            # Await publication before executing the selected target so Agent
            # history can show selection while that target is still generating.
            await observer(name, state, data)

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
            if runtime:
                runtime.send = send
                await send("context", runtime.context)
            route_body = routing.AutoRoute(
                **body.model_dump(
                    exclude={"execute_target", "session_id", "template_id", "client_request_id", "session", "history"}
                )
            )
            route = await routing.run_classified_route(
                route_body,
                request,
                infer=request.app.state.gateway_decision.infer,
                emit=progress,
                model_select=request.app.state.gateway_decision.select,
                joint_decide=(request.app.state.gateway_decision.assess
                              if request.app.state.gateway_decision.routing_mode == "joint" else None),
            )
            await send("route", route)
            executed = None
            if route["status"] == "selected" and body.execute_target:
                stage = "execution"
                await progress(stage, "started", {})
                executed = await routing.auto_execute(
                    routing.AutoExecute(route_token=route["steps"][1]["route_token"]), request
                )
                if runtime:
                    await run_in_threadpool(runtime.save_success, record, executed)
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
        if runtime:
            with contextlib.suppress(Exception):
                await asyncio.shield(run_in_threadpool(
                    history.records.release_lease, runtime.pk, "LEASE#" + runtime.session_id, request_id,
                ))


@router.post("/runs")
async def run(body: GatewayRun, request: Request):
    if body.history is not None and not hasattr(request.app.state, "history"):
        raise HTTPException(422, "此入口尚未启用多轮会话，请使用独立模型网关。")
    body = body.model_copy(
        update={"provider": body.provider or request.app.state.gateway_decision.default_provider}
    )
    # Reject stale clients before opening the stream or spending inference resources.
    routing.check_version(routing.load_catalog(), body.catalog_version)
    routing.check_ranking_version(routing.load_rankings(), body.ranking_version)
    if body.policy_version != SELECTOR_VERSION:
        raise HTTPException(409, "分类选型策略已更新，请刷新。")
    if body.task.attachments:
        if not hasattr(request.app.state, "media"):
            raise HTTPException(503, "当前服务尚未配置媒体存储。")
        await run_in_threadpool(
            request.app.state.media.resolve, request.state.user["username"], body.task.attachments,
        )
    if body.mode == "live":
        request.app.state.gateway_decision.service(body.provider)
        if body.execute_target and request.app.state.bedrock is None:
            raise HTTPException(503, "Bedrock 尚未配置；可选择仅分析路由。")
    run_id = secrets.token_hex(12)
    record = None
    if history := getattr(request.app.state, "history", None):
        lease = None
        try:
            if body.mode == "live":
                user = request.state.user["username"]
                if body.session_id:
                    await run_in_threadpool(history.session, user, body.session_id)
                else:
                    session = await run_in_threadpool(history.create_session, user, "未命名会话")
                    body = body.model_copy(update={"session_id": session["id"]})
                pk, sk = history.user_key(user), "LEASE#" + body.session_id
                await run_in_threadpool(history.records.acquire_lease, pk, sk, run_id)
                lease = (pk, sk)
                runtime = await run_in_threadpool(Conversation, body, history, user, run_id)
                task = runtime.task(body.task)
                if task.attachments:
                    await run_in_threadpool(request.app.state.media.resolve, user, task.attachments)
                request.state.gateway_runtime = runtime
            record = await run_in_threadpool(
                history.begin,
                request.state.user["username"],
                body,
                run_id,
            )
        except Exception as exc:
            if lease:
                await run_in_threadpool(history.records.release_lease, *lease, run_id)
            if isinstance(exc, HTTPException):
                raise
            logger.exception("Cannot create persisted run")
            raise HTTPException(503, "历史数据库不可用，尚未调用模型。") from exc
        if body.mode == "live":
            body = body.model_copy(update={"task": task})
    return StreamingResponse(
        event_stream(body, request, run_id=run_id, record=record),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
