"""AgentCore HTTP contract; every Strands model call goes through Model Router."""

import asyncio
import contextlib
import copy
import json
import os
from time import perf_counter

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from strands import Agent, tool
from strands.models.model import Model

from app.industry_scenarios import catalog, invoke_tool, scenario, system_prompt

app = FastAPI(docs_url=None, openapi_url=None)


class Invocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str
    dataset_version: str
    prompt: str = Field(min_length=1, max_length=1400)
    messages: list[dict] = Field(default_factory=list, max_length=80)
    callback_token: str = Field(min_length=32, max_length=128, repr=False)
    max_model_calls: int = Field(default=6, ge=1, le=6)


class RouterModel(Model):
    """A model provider, not a tool wrapping a second fixed-model agent."""

    def __init__(self, client, token, emit, maximum):
        self.client, self.token, self.emit = client, token, emit
        self.maximum, self.calls = maximum, 0
        self.config = {"model_id": "model-router", "context_window_limit": 96_000}

    def get_config(self):
        return dict(self.config)

    def update_config(self, **model_config):
        if model_config:
            raise ValueError("Agent模型固定为model-router；目标模型由网关选择")

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        raise NotImplementedError("此接口使用原生工具调用，不提供额外结构化输出协议")
        yield  # pragma: no cover

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        if self.calls >= self.maximum:
            raise RuntimeError("已达到本轮模型调用上限，保留过程记录")
        self.calls += 1
        await self.emit({"type": "model_started", "step": self.calls, "model": "model-router"})
        response = await self.client.post(
            "/api/gateway/agent/model",
            headers={"Authorization": "Bearer " + self.token, "Accept": "application/x-ndjson"},
            json={"step": self.calls, "messages": messages},
        )
        if response.status_code != 200:
            # Never include request headers/tokens in an exception or public trace.
            raise RuntimeError(f"Model Router调用未完成（HTTP {response.status_code}），请查看网关步骤历史")
        output = response.json()
        if error := output.get("error"):
            raise RuntimeError(f"Model Router步骤未完成（{error['code']}），请查看网关步骤历史")
        await self.emit({"type": "model_completed", **output["routing"]})
        yield {"messageStart": {"role": "assistant"}}
        for index, block in enumerate(output["message"]["content"]):
            if "text" in block:
                yield {"contentBlockDelta": {"contentBlockIndex": index, "delta": {"text": block["text"]}}}
            elif "toolUse" in block:
                call = block["toolUse"]
                yield {"contentBlockStart": {"contentBlockIndex": index, "start": {
                    "toolUse": {"toolUseId": call["toolUseId"], "name": call["name"]}}}}
                yield {"contentBlockDelta": {"contentBlockIndex": index, "delta": {
                    "toolUse": {"input": json.dumps(call["input"], ensure_ascii=False)}}}}
            yield {"contentBlockStop": {"contentBlockIndex": index}}
        yield {"messageStop": {"stopReason": output["stop_reason"]}}
        yield {"metadata": {"usage": output.get("usage") or {},
                            "metrics": {"latencyMs": output["routing"]["target_ms"]}}}


def build_tools(item, emit):
    async def call(name, arguments):
        started = perf_counter()
        await emit({"type": "tool_started", "name": name, "arguments": arguments})
        try:
            result = invoke_tool(item, name, arguments)
        except ValueError as exc:
            await emit({"type": "tool_result", "name": name, "arguments": arguments,
                        "status": "error", "result": str(exc),
                        "elapsed_ms": round((perf_counter() - started) * 1000, 3)})
            return {"status": "error", "content": [{"text": str(exc)}]}
        await emit({"type": "tool_result", "name": name, "arguments": arguments,
                    "status": "success", "result": result,
                    "elapsed_ms": round((perf_counter() - started) * 1000, 3)})
        return result

    @tool
    async def query_records(dataset: str) -> dict:
        """查询场景的只读业务数据。dataset为已声明的数据集名称。"""
        return await call("query_records", {"dataset": dataset})

    @tool
    async def calculate_metric(dataset: str, field: str, operation: str) -> dict:
        """计算数值列，operation为sum、mean、min、max、count之一。"""
        return await call("calculate_metric", {"dataset": dataset, "field": field, "operation": operation})

    @tool
    async def read_policy() -> dict:
        """读取当前行业场景的业务规则与边界。"""
        return await call("read_policy", {})

    return [query_records, calculate_metric, read_policy]


@app.get("/ping")
def ping():
    return {"status": "Healthy"}


@app.post("/invocations")
async def invoke(body: Invocation):
    item = scenario(body.scenario_id)
    if not item or body.dataset_version != catalog()["version"]:
        raise HTTPException(409, "行业数据版本不一致")
    url = os.environ["MODEL_ROUTER_URL"].rstrip("/")
    queue = asyncio.Queue(maxsize=16)

    async def work():
        try:
            async with asyncio.timeout(590), httpx.AsyncClient(
                base_url=url, timeout=httpx.Timeout(200, connect=10), trust_env=False,
            ) as client:
                model = RouterModel(client, body.callback_token, queue.put, body.max_model_calls)
                agent = Agent(model=model, tools=build_tools(item, queue.put),
                              messages=body.messages, system_prompt=system_prompt(item),
                              callback_handler=None, retry_strategy=None, context_manager=False,
                              name="行业业务Agent")
                await queue.put({"type": "started", "model": "model-router",
                                 "scenario_id": item["id"], "data_source": "synthetic_business_records"})
                async for _ in agent.stream_async(body.prompt):
                    pass
                answer = "\n".join(b["text"] for b in agent.messages[-1]["content"] if "text" in b)
                if not answer.strip() or any("toolUse" in b for b in agent.messages[-1]["content"]):
                    raise RuntimeError("Agent尚未生成完整最终答复")
                # Strands stores tracking_id and metrics on its own message objects.
                # Persist the same native role/content contract used by model.stream().
                transcript = [
                    {"role": m["role"], "content": copy.deepcopy(m["content"])}
                    for m in agent.messages
                ]
                await queue.put({"type": "completed", "answer": answer, "messages": transcript,
                                 "model_calls": model.calls})
        except Exception as exc:
            await queue.put({"type": "error", "message": str(exc)[:500]})
        finally:
            if not asyncio.current_task().cancelling():
                await queue.put(None)

    async def stream():
        task = asyncio.create_task(work())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), 5)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if event is None:
                    break
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return StreamingResponse(stream(), media_type="text/event-stream")
