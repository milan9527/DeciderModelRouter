"""User-owned AgentCore jobs and narrowly scoped callbacks to the model router."""

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import secrets
import time

import boto3
from botocore.config import Config
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from app import routing
from app.agent_model import AgentConversation, validate_native_messages
from app.gateway import GatewayRun, event_stream
from app.gateway_store import clean
from app.industry_scenarios import catalog, scenario
from app.routing_balance import RoutingPreferences
from app.routing_selector import SELECTOR_VERSION
from app.schemas import StrictModel

router = APIRouter(prefix="/api/gateway/agent", tags=["Industry agents"])
logger = logging.getLogger(__name__)
MAX_CALLS = 6
MODEL_HEARTBEAT_SECONDS = 5
AGENT_PROTOCOL_VERSION = "native-stream-v1"


def retained_history(messages):
    """Trim old whole turns, never silently discard the most recent answer."""
    kept, trimmed = messages, 0
    while kept and (len(json.dumps(kept, ensure_ascii=False).encode()) > 64_000 or len(kept) > 64):
        boundary = next((i for i, m in enumerate(kept[1:], 1)
                         if m["role"] == "user" and all("text" in b for b in m["content"])), None)
        if boundary is None:
            break
        kept = kept[boundary:]
        trimmed += 1
    # Leave space for the new prompt and native tool loop within the 96KB boundary.
    if len(json.dumps(kept, ensure_ascii=False).encode()) > 80_000 or len(kept) > 64:
        raise HTTPException(422, "最近一轮完整对话过长，无法继续携带；请新建会话并提供所需摘要。")
    return kept, trimmed


class AgentTurn(StrictModel):
    scenario_id: str = Field(max_length=80)
    prompt: str = Field(min_length=1, max_length=1400)
    session_id: str | None = Field(None, pattern=r"^[a-f0-9]{24}$")
    client_request_id: str = Field(pattern=r"^[a-zA-Z0-9-]{16,64}$")
    routing_preferences: RoutingPreferences = Field(default_factory=lambda: RoutingPreferences(
        cost_tier="medium", expected_output_tokens=1024, max_output_tokens=16384,
    ))


class ModelStep(StrictModel):
    step: int = Field(ge=1, le=MAX_CALLS)
    messages: list[dict] = Field(min_length=1, max_length=80)


def millis():
    return int(time.time() * 1000)


class Agents:
    def __init__(self, history, auth):
        self.history, self.records, self.auth = history, history.records, auth
        self.arn = os.getenv("GATEWAY_AGENT_RUNTIME_ARN", "")
        self.qualifier = os.getenv("GATEWAY_AGENT_RUNTIME_QUALIFIER", "DEFAULT")
        self.client = boto3.client("bedrock-agentcore", region_name=os.getenv("AWS_REGION", "us-east-1"),
                                   config=Config(connect_timeout=10, read_timeout=610,
                                                 retries={"total_max_attempts": 1})) if self.arn else None
        self.tasks = {}
        self.lock = asyncio.Lock()

    @staticmethod
    def key(user):
        return "USER#" + user

    @staticmethod
    def job_key(user, job_id):
        return "AGENTJOB#" + user + "#" + job_id

    def save(self, user, job):
        job["updated_at"] = millis()
        self.records.put_many([
            (self.key(user), "AGENTJOB#" + job["id"], job),
            self.index_entry(user, job),
        ])

    def index_entry(self, user, job):
        return (self.key(user), f"AGENTLIST#{job['created_at']:013d}#{job['id']}",
                {"id": job["id"], "created_at": job["created_at"],
                 "expires_at": job["created_at"] // 1000 + 180 * 86400})

    def ensure_index(self, user):
        """Migrate earlier job keys once; random IDs cannot identify the latest 100."""
        pk = self.key(user)
        if self.records.get(pk, "AGENTINDEX#v1"):
            return
        cursor = None
        while True:
            page = self.records.query(pk, "AGENTJOB#", limit=100, cursor=cursor)
            if page["items"]:
                self.records.put_many([self.index_entry(user, job) for job in page["items"]])
            cursor = page["next_cursor"]
            if not cursor:
                break
        self.records.put(pk, "AGENTINDEX#v1", {"version": 1})

    def get(self, user, job_id):
        job = self.records.get(self.key(user), "AGENTJOB#" + job_id)
        if not job:
            raise HTTPException(404, "Agent任务不存在")
        if job["status"] == "running" and job["created_at"] < millis() - 660_000:
            job.update(status="interrupted", error="执行超时或服务中断；未自动重新调用模型。")
            self.save(user, job)
        return job

    def detail(self, user, job_id):
        job = self.get(user, job_id)
        result = {k: v for k, v in job.items() if k not in {"account_version"}}
        result["steps"] = self.records.query(self.job_key(user, job_id), "STEP#", limit=10, reverse=False)["items"]
        result["events"] = self.records.query(self.job_key(user, job_id), "EVENT#", limit=100, reverse=False)["items"]
        result["metrics"] = {
            "model_calls": len(result["steps"]),
            "tool_calls": sum(e["type"] == "tool_result" for e in result["events"]),
            "model_switches": sum(s.get("model_changed", False) for s in result["steps"]),
            "retained_calls": sum(s.get("stickiness", {}).get("retained", False) for s in result["steps"]),
            "cache_read_tokens": sum(s.get("cache", {}).get("read_tokens", 0) for s in result["steps"]),
        }
        return clean(result)

    def identity(self, authorization):
        if not authorization.startswith("Bearer ") or not 32 <= len(authorization[7:]) <= 128:
            raise HTTPException(401, "Agent调用凭据无效")
        digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
        scope = self.records.get("AGENTCAP", digest)
        if not scope:
            raise HTTPException(401, "Agent调用凭据失效")
        job = self.get(scope["username"], scope["job_id"])
        account = self.auth.store.get("USER#" + scope["username"])
        if (job["status"] != "running" or not account or not account["enabled"]
                or account["version"] != job["account_version"]):
            raise HTTPException(401, "Agent任务或账户已失效")
        return {"username": scope["username"], "expires_at": scope["expires_at"]}, job

    async def start(self, body, request):
        if not self.client:
            raise HTTPException(503, "AgentCore Runtime尚未配置")
        item = scenario(body.scenario_id)
        if not item or not body.prompt.strip():
            raise HTTPException(422, "请选择有效行业场景并填写任务")
        user = request.state.user["username"]
        pk = self.key(user)
        async with self.lock:
            if len(self.tasks) >= 2:
                raise HTTPException(429, "Agent正在处理其他任务，请稍后重试")
            if any(owner == user for owner, _ in self.tasks):
                raise HTTPException(409, "当前账户已有Agent任务运行中")
            prior = await run_in_threadpool(self.records.get, pk, "AGENTIDEMP#" + body.client_request_id)
            if prior:
                return await run_in_threadpool(self.detail, user, prior["job_id"])
            if body.session_id:
                session = await run_in_threadpool(self.records.get, pk, "AGENTSESSION#" + body.session_id)
                if not session or session["scenario_id"] != body.scenario_id:
                    raise HTTPException(404, "Agent会话不存在或不属于此行业场景")
            else:
                session = await run_in_threadpool(self.history.create_session, user, "Agent · " + item["title"])
                session.update(scenario_id=body.scenario_id, messages=[], turns=0,
                               dataset_version=catalog()["version"])
                await run_in_threadpool(self.records.put, pk, "AGENTSESSION#" + session["id"], session)
            if session["dataset_version"] != catalog()["version"]:
                raise HTTPException(409, "行业数据版本已更新，请新建Agent会话")
            messages, trimmed = retained_history(session["messages"])
            session = {**session, "messages": messages}
            job_id, token = secrets.token_hex(12), secrets.token_urlsafe(32)
            await run_in_threadpool(self.records.acquire_lease, pk, "AGENTLEASE#" + session["id"], job_id, 660)
            try:
                account = await run_in_threadpool(self.auth.store.get, "USER#" + user)
                job = {"id": job_id, "session_id": session["id"], "scenario_id": item["id"],
                       "title": item["title"], "industry": item["industry"], "prompt": body.prompt,
                       "status": "running", "created_at": millis(), "account_version": account["version"],
                       "turn": session["turns"] + 1, "runtime_arn": self.arn,
                       "runtime_qualifier": self.qualifier, "platform_version": "V2",
                       "protocol_version": AGENT_PROTOCOL_VERSION,
                       "model": "model-router", "provider": request.app.state.gateway_decision.default_provider,
                       "dataset_version": catalog()["version"], "data_source": "synthetic_business_records",
                       "routing_preferences": body.routing_preferences.model_dump(mode="json"),
                       "trimmed_turns": trimmed, "context_messages": len(messages),
                       "context_bytes": len(json.dumps(messages, ensure_ascii=False).encode()),
                       "max_model_calls": MAX_CALLS}
                await run_in_threadpool(self.save, user, job)
                await run_in_threadpool(self.records.put, "AGENTCAP", hashlib.sha256(token.encode()).hexdigest(),
                                       {"username": user, "job_id": job_id, "expires_at": int(time.time()) + 660},
                                       create=True)
                await run_in_threadpool(self.records.put, pk, "AGENTIDEMP#" + body.client_request_id,
                                       {"job_id": job_id, "expires_at": int(time.time()) + 86400}, create=True)
                task = asyncio.create_task(self.work(user, job, session, token))
                self.tasks[user, job_id] = task
                task.add_done_callback(lambda _: self.tasks.pop((user, job_id), None))
            except Exception:
                await run_in_threadpool(self.records.release_lease, pk, "AGENTLEASE#" + session["id"], job_id)
                raise
        return await run_in_threadpool(self.detail, user, job_id)

    async def work(self, user, job, session, token):
        queue = asyncio.Queue(maxsize=16)
        loop = asyncio.get_running_loop()
        holder = {}
        stopped = __import__("threading").Event()
        messages = session["messages"]
        payload = {"scenario_id": job["scenario_id"], "dataset_version": job["dataset_version"],
                   "prompt": job["prompt"], "messages": messages, "callback_token": token,
                   "max_model_calls": MAX_CALLS}
        # User ownership and runtime identity are set here, never accepted from the browser.
        runtime_session = "dmr-" + AGENT_PROTOCOL_VERSION + "-" + hashlib.sha256(
            (user + ":" + session["id"]).encode()).hexdigest()
        job["runtime_session_id"] = runtime_session
        await run_in_threadpool(self.save, user, job)

        def produce():
            stream = None
            try:
                response = self.client.invoke_agent_runtime(
                    agentRuntimeArn=self.arn, qualifier=self.qualifier, runtimeSessionId=runtime_session,
                    contentType="application/json", accept="text/event-stream",
                    payload=json.dumps(payload, ensure_ascii=False).encode(),
                )
                stream = response["response"]
                holder["stream"] = stream
                for line in stream.iter_lines():
                    if stopped.is_set():
                        break
                    if line.startswith(b"data: "):
                        event = json.loads(line[6:])
                        future = asyncio.run_coroutine_threadsafe(queue.put(event), loop)
                        future.result(timeout=15)
                if not stopped.is_set():
                    asyncio.run_coroutine_threadsafe(queue.put(None), loop).result(timeout=15)
            except Exception:
                logger.exception("AgentCore invocation failed: %s", job["id"])
                if not stopped.is_set():
                    asyncio.run_coroutine_threadsafe(queue.put(
                        {"type": "error", "message": "AgentCore调用失败，请凭任务ID核查。"}
                    ), loop).result(timeout=15)
            finally:
                if stream:
                    stream.close()

        producer = asyncio.create_task(asyncio.to_thread(produce))
        sequence = 0
        try:
            async with asyncio.timeout(600):
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    sequence += 1
                    if sequence > 100:
                        raise ValueError("Agent事件超过上限")
                    kind = event.get("type")
                    if kind == "completed":
                        messages = event.pop("messages")
                        # Validate the full transcript by adding a non-persisted next user turn.
                        validate_native_messages([*messages, {"role": "user", "content": [{"text": "next"}]}])
                        session.update(messages=messages, turns=job["turn"], updated_at=millis())
                        await run_in_threadpool(self.records.put, self.key(user), "AGENTSESSION#" + session["id"], session)
                        job.update(status="completed", answer=event["answer"], finished_at=millis())
                    if kind == "error":
                        job.update(status="error", error=event.get("message", "Agent执行失败"), finished_at=millis())
                    event.update(sequence=sequence, recorded_at=millis())
                    await run_in_threadpool(self.records.put, self.job_key(user, job["id"]),
                                           f"EVENT#{sequence:04d}", event, create=True)
                    if kind in {"completed", "error"}:
                        break
                if job["status"] == "running":
                    job.update(status="interrupted", error="Agent流未返回终态，未自动重新执行。")
        except asyncio.CancelledError:
            job.update(status="interrupted", error="服务停止，保留已完成调用，未自动重跑。")
            raise
        except Exception:
            logger.exception("Agent job failed: %s", job["id"])
            job.update(status="error", error="Agent执行或保存失败，保留已返回结果。")
        finally:
            stopped.set()
            if stream := holder.get("stream"):
                stream.close()
            producer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await producer
            await run_in_threadpool(self.save, user, job)
            await run_in_threadpool(self.records.release_lease, self.key(user),
                                   "AGENTLEASE#" + session["id"], job["id"])

    async def close(self):
        for task in list(self.tasks.values()):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)
        if self.client:
            self.client.close()


@router.get("/scenarios")
def scenarios(request: Request):
    agents = request.app.state.agents
    return {**catalog(), "configured": bool(agents.arn), "model": "model-router",
            "platform_version": "V2", "max_model_calls_per_turn": MAX_CALLS}


@router.post("/jobs", status_code=202)
async def start(body: AgentTurn, request: Request):
    return await request.app.state.agents.start(body, request)


@router.get("/jobs")
def jobs(request: Request):
    a, user = request.app.state.agents, request.state.user["username"]
    a.ensure_index(user)
    rows = a.records.query(a.key(user), "AGENTLIST#", limit=100)["items"]
    return {"items": [{k: v for k, v in a.get(user, j["id"]).items()
                       if k not in {"account_version", "answer"}} for j in rows]}


@router.get("/jobs/{job_id}")
def detail(job_id: str, request: Request):
    return request.app.state.agents.detail(request.state.user["username"], job_id)


@router.post("/model")
async def model(body: ModelStep, request: Request):
    if request.headers.get("accept") != "application/x-ndjson":
        return await model_step(body, request)

    async def stream():
        task = asyncio.create_task(model_step(body, request))
        try:
            yield "\n"
            while not task.done():
                done, _ = await asyncio.wait({task}, timeout=MODEL_HEARTBEAT_SECONDS)
                if not done:
                    yield "\n"
            try:
                result = task.result()
            except HTTPException as exc:
                result = {"error": {"code": exc.status_code, "message": exc.detail}}
            except Exception:
                logger.exception("Agent model callback failed")
                result = {"error": {"code": 500, "message": "Agent模型步骤失败，请查看历史"}}
            yield json.dumps(clean(result), ensure_ascii=False) + "\n"
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    # Whitespace is valid before the one final JSON value. The native client
    # buffers it as JSON, while proxies receive real bytes throughout inference.
    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


async def model_step(body: ModelStep, request: Request):
    """Only a short-lived capability for an active, user-owned job can reach this endpoint."""
    a, job = request.app.state.agents, request.state.agent_job
    user = request.state.user["username"]
    try:
        validate_native_messages(body.messages)
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from exc
    key, sk = a.job_key(user, job["id"]), f"STEP#{body.step:04d}"
    cached = await run_in_threadpool(a.records.get, key, "RESPONSE#" + sk)
    fingerprint = hashlib.sha256(json.dumps(body.messages, sort_keys=True).encode()).hexdigest()
    if cached:
        if cached["fingerprint"] != fingerprint:
            raise HTTPException(409, "同一步骤不可提交不同上下文")
        return cached["response"]
    if body.step > 1 and not await run_in_threadpool(a.records.get, key, f"RESPONSE#STEP#{body.step - 1:04d}"):
        raise HTTPException(409, "Agent模型调用必须按顺序进行")
    await run_in_threadpool(a.records.acquire_lease, key, "CALL", sk, 300)
    run_id = secrets.token_hex(12)
    pk, lease = a.history.user_key(user), "LEASE#" + job["session_id"]
    acquired = False
    try:
        # A failed or partially completed step cannot be silently retried.
        prior = await run_in_threadpool(a.records.get, key, sk)
        if prior:
            raise HTTPException(409, "此步骤已经执行或结果未知，请查看历史，未重复调用模型")
        await run_in_threadpool(a.records.put, key, sk, {"step": body.step, "run_id": run_id, "status": "started"}, create=True)
        await run_in_threadpool(a.records.acquire_lease, pk, lease, run_id, 300)
        acquired = True
        data, rankings = routing.load_catalog(), routing.load_rankings()
        run = GatewayRun(task=routing.Task(text=job["prompt"],
                                          output_modality="text", routing_preferences=job["routing_preferences"]),
                         provider=job["provider"], mode="live", execute_target=True,
                         session_id=job["session_id"], client_request_id=job["id"] + "-" + str(body.step),
                         template_id="agent-" + job["scenario_id"], catalog_version=data["version"],
                         ranking_version=rankings["version"], policy_version=SELECTOR_VERSION,
                         session={"use_history": False, "stickiness": True, "prompt_cache": True,
                                  "stream": False, "fallback": True})
        runtime = await run_in_threadpool(AgentConversation, run, a.history, user, run_id,
                                         body.messages, scenario(job["scenario_id"]))
        progress = {"step": body.step, "run_id": run_id, "status": "started",
                    "phase": "preparing", "routing_started_at": millis(),
                    "routing_context_version": "agent-initial-decision-v1"}

        async def publish_progress(stage, status, data):
            if stage == "classification":
                progress["phase"] = "assessing" if status == "started" else "selecting"
                if status == "completed":
                    progress["profile"] = data["profile"]
                    progress["decision_model"] = data["model"]
            elif stage == "selection":
                progress["phase"] = "selecting" if status == "started" else "selected"
                if status == "completed":
                    selected, selection = data.get("selected_model") or {}, data["selection"]
                    progress.update(
                        model_id=selected.get("id"), configuration_id=selection.get("selected_candidate_id"),
                        selected_at=millis() if selected else None, selection_reason=selection.get("reason"),
                        stickiness=selection.get("stickiness"), decision_mode=selection.get("decision_mode"),
                        model_decision_ms=selection.get("decision_ms"),
                        model_decision_calls=len(selection.get("decision_calls", [])),
                        assessment_ms=selection.get("assessment_ms"), selection_ms=selection.get("selection_ms"),
                    )
            elif stage == "execution":
                progress["phase"] = "executing" if status == "started" else "completed"
                progress["target_started_at" if status == "started" else "target_finished_at"] = millis()
            else:
                return
            await run_in_threadpool(a.records.put, key, sk, dict(progress))

        runtime.route_progress = publish_progress
        request.state.gateway_runtime = runtime
        record = await run_in_threadpool(a.history.begin, user, run, run_id)
        run = run.model_copy(update={"task": runtime.task(run.task)})
        events = []
        async for line in event_stream(run, request, run_id=run_id, record=record):
            if line.strip():
                events.append(json.loads(line))
        if events[-1]["type"] != "completed" or events[-1]["data"]["status"] != "executed":
            failed = {**progress, "step": body.step, "run_id": run_id, "status": "failed", "phase": "failed",
                      "error": events[-1]["data"]}
            await run_in_threadpool(a.records.put, key, sk, failed)
            raise HTTPException(502, "Agent模型步骤未完成，请查看该步骤历史记录")
        output = next(e["data"] for e in events if e.get("stage") == "execution" and e.get("status") == "completed")
        decision = next(e["data"] for e in events if e.get("stage") == "classification" and e.get("status") == "completed")
        selection = next(e["data"] for e in events if e.get("stage") == "selection" and e.get("status") == "completed")
        previous_config = runtime.previous.get("configuration_id") if runtime.previous else None
        previous_model = runtime.previous.get("model_id") if runtime.previous else None
        summary = {**progress, "step": body.step, "run_id": run_id, "status": "completed", "provider": job["provider"],
                   "decision_model": decision["model"], "profile": decision["profile"],
                   "model_id": output["model_id"], "configuration_id": output["configuration_id"],
                   "previous_model_id": previous_model, "previous_configuration_id": previous_config,
                   "model_changed": bool(previous_model and previous_model != output["model_id"]),
                   "configuration_changed": bool(previous_config and previous_config != output["configuration_id"]),
                   "stickiness": selection["selection"].get("stickiness", {}),
                   "selection_reason": selection["selection"]["reason"],
                   "model_decision_ms": selection["selection"].get("decision_ms"),
                   "model_decision_calls": len(selection["selection"].get("decision_calls", [])),
                   "decision_mode": selection["selection"].get("decision_mode"),
                   "measurement_version": selection["selection"].get("measurement_version"),
                   "gpu_ms": sum(c["timings"]["upstream_ms"]
                                 for c in selection["selection"].get("decision_calls", [])),
                   "assessment_ms": selection["selection"].get("assessment_ms"),
                   "selection_ms": selection["selection"].get("selection_ms"),
                   "target_ms": output["backend_total_ms"],
                   "usage": output["usage"], "cache": output["cache"],
                   "output_budget": output.get("output_budget"),
                   "provider_metadata_omitted": output.get("provider_metadata_omitted"),
                   "reference_cost_usd": output["observed_reference_cost_usd"],
                   "stop_reason": output["stop_reason"], "tool_call_count": len(output["tool_calls"])}
        response = {"message": output["message"], "stop_reason": output["stop_reason"],
                    "usage": output["usage"], "routing": summary}
        await run_in_threadpool(a.records.put, key, sk, summary)
        await run_in_threadpool(a.records.put, key, "RESPONSE#" + sk,
                               {"fingerprint": fingerprint, "response": response, "expires_at": int(time.time()) + 660}, create=True)
        return response
    finally:
        if acquired:
            await run_in_threadpool(a.records.release_lease, pk, lease, run_id)
        await run_in_threadpool(a.records.release_lease, key, "CALL", sk)
