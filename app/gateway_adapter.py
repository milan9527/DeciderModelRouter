"""Gateway Converse adapter: role-preserving input, native cache and bounded streaming."""

import asyncio
import copy
import queue
import threading
import time

from botocore.exceptions import ClientError
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from app.bedrock_limits import output_limit
from app.routing_balance import preferences

CACHE_SOURCE = "https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html"
# Exact IDs only. A provider name or Converse support alone does not imply cache support.
CACHE_MODELS = {
    "amazon.nova-micro-v1:0": (1024, 20_000),
    "amazon.nova-lite-v1:0": (1024, 20_000),
    "amazon.nova-pro-v1:0": (1024, 20_000),
    "amazon.nova-2-lite-v1:0": (1024, 20_000),
    "anthropic.claude-sonnet-4-5-20250929-v1:0": (1024, None),
    "anthropic.claude-sonnet-4-6": (1024, None),
    "anthropic.claude-haiku-4-5-20251001-v1:0": (4096, None),
    "anthropic.claude-opus-4-5-20251101-v1:0": (4096, None),
    "anthropic.claude-opus-4-6-v1": (4096, None),
    "anthropic.claude-sonnet-5": (512, None),
    "anthropic.claude-sonnet-5-5": (512, None),
}


def capabilities(row):
    adapter = row.get("adapter") or "converse"
    conversational = adapter in {"converse", "converse_media"} and "TEXT" in row["model"]["output_modalities"]
    return {"adapter": adapter, "multi_turn": conversational,
            "stream": conversational and row["model"].get("streaming") is True,
            "explicit_prompt_cache": conversational and row["model"]["id"] in CACHE_MODELS}


def cache_usage(usage, plan):
    usage = usage or {}
    read, write = usage.get("cacheReadInputTokens"), usage.get("cacheWriteInputTokens")
    def valid(value):
        return type(value) is int and value >= 0
    return {**plan, "observed": valid(read) or valid(write),
            "read_tokens": read if valid(read) else 0,
            "write_tokens": write if valid(write) else 0,
            "total_input_tokens": usage.get("inputTokens", 0) + (read or 0) + (write or 0)
            if valid(usage.get("inputTokens")) else None,
            "note": "命中以 Bedrock cacheReadInputTokens 为准；inputTokens 不包含缓存读写。缓存费用未知时不估算折扣。"}


def request_args(row, task, runtime, resolved):
    model = row["model"]
    messages = []
    for message in runtime.messages:
        content = [{"text": message["text"]}]
        for ref in message.get("attachments", []):
            metadata, raw = resolved[ref["id"]]
            content.append({metadata["kind"]: {"format": metadata["format"], "source": {"bytes": raw}}})
        messages.append({"role": message["role"], "content": content})
    plan = {"enabled": runtime.options.prompt_cache, "supported": model["id"] in CACHE_MODELS,
            "checkpoints": 0, "source": CACHE_SOURCE}
    if plan["enabled"] and plan["supported"]:
        minimum, maximum = CACHE_MODELS[model["id"]]
        plan.update(minimum_tokens=minimum, ttl_seconds=300)
        # Keep earlier turn checkpoints when appending a turn, so short first
        # messages can still benefit once their cumulative prefix reaches the minimum.
        # Nova's 20K cache limit is bounded conservatively by UTF-8 bytes; skip
        # media prefixes because their tokenization cannot be inferred from file size.
        prefix_bytes, contains_media = 0, False
        for i, message in enumerate(messages):
            original = runtime.messages[i]
            prefix_bytes += len(original["text"].encode()) + 64
            contains_media |= bool(original.get("attachments"))
            if i not in {0, len(messages) - 2, len(messages) - 4, len(messages) - 6}:
                continue
            if maximum and (prefix_bytes > maximum - 1024 or contains_media):
                continue
            message["content"].append({"cachePoint": {"type": "default"}})
            plan["checkpoints"] += 1
    kwargs = {"modelId": model["invoke_id"], "messages": messages,
              "inferenceConfig": {**row["benchmark"].get("inference_config", {}),
                                  "maxTokens": output_limit(model, preferences(task).max_output_tokens)}}
    if row["benchmark"].get("request_fields"):
        kwargs["additionalModelRequestFields"] = copy.deepcopy(row["benchmark"]["request_fields"])
    return kwargs, plan


def final_result(text, usage, stop, cache, streaming, ttft=None):
    return {"text": text, "has_final_text": bool(text.strip()),
            "complete": bool(text.strip()) and stop == "end_turn", "stop_reason": stop,
            "usage": usage, "cache": cache_usage(usage, cache), "streamed": streaming,
            "ttft_ms": ttft}


async def converse(request, client, row, task, runtime):
    if getattr(runtime, "agent_tools", False):
        from app.agent_model import agent_converse

        return await agent_converse(client, row, task, runtime)
    caps = capabilities(row)
    if not caps["multi_turn"]:
        raise HTTPException(422, "当前适配器不支持多轮文本会话，请关闭历史或新建会话。")
    resolved = {}
    if task.attachments:
        values = [await run_in_threadpool(request.app.state.media.read, runtime.user, ref.id)
                  for ref in task.attachments]
        resolved = {ref.id: value for ref, value in zip(task.attachments, values, strict=True)}
    kwargs, cache = request_args(row, task, runtime, resolved)
    if not runtime.options.stream or not caps["stream"]:
        response = await run_in_threadpool(client.converse, **kwargs)
        text = "\n".join(c["text"] for c in response["output"]["message"]["content"] if "text" in c)
        return final_result(text, response.get("usage"), response.get("stopReason"), cache, False)

    # boto3 event streams are synchronous. Bound the bridge, close on cancellation,
    # and batch token deltas to avoid a database transaction per individual token.
    bridge, cancelled = queue.Queue(maxsize=8), threading.Event()
    holder = {}
    started = time.perf_counter()

    def put(item):
        while not cancelled.is_set():
            try:
                bridge.put(item, timeout=0.2)
                return
            except queue.Full:
                continue

    def produce():
        stream = None
        text, pending, usage, stop, first = "", "", None, None, None
        flushed = time.perf_counter()
        try:
            response = client.converse_stream(**kwargs)
            stream = response["stream"]
            holder["stream"] = stream
            for event in stream:
                if cancelled.is_set():
                    break
                for key, value in event.items():
                    if key.endswith("Exception"):
                        raise ClientError({"Error": {"Code": key[0].upper() + key[1:],
                                                      "Message": value.get("message", "")},
                                           "ResponseMetadata": response.get("ResponseMetadata", {})}, "ConverseStream")
                if (event.get("contentBlockDelta", {}).get("delta")
                        or event.get("contentBlockStart", {}).get("start")):
                    # Reasoning/tool output is progress too, even before visible text.
                    holder["partial"] = True
                delta = event.get("contentBlockDelta", {}).get("delta", {}).get("text", "")
                if delta:
                    if first is None:
                        first = round((time.perf_counter() - started) * 1000, 3)
                    text += delta
                    pending += delta
                    # Signal partial output before any exception can trigger fallback.
                    holder["partial"] = True
                if pending and (len(pending) >= 256 or time.perf_counter() - flushed >= 0.12):
                    put(("delta", pending))
                    pending, flushed = "", time.perf_counter()
                stop = event.get("messageStop", {}).get("stopReason", stop)
                usage = event.get("metadata", {}).get("usage", usage)
            if pending:
                put(("delta", pending))
                pending = ""
            if not cancelled.is_set():
                if stop is None:
                    raise ValueError("Stream ended without messageStop")
                put(("result", final_result(text, usage, stop, cache, True, first)))
        except Exception as exc:
            if pending:
                put(("delta", pending))
            put(("error", exc))
        finally:
            if stream is not None:
                stream.close()

    thread = threading.Thread(target=produce, daemon=True, name="bedrock-converse")
    thread.start()
    try:
        while True:
            try:
                kind, data = await asyncio.to_thread(bridge.get, True, 0.2)
            except queue.Empty:
                continue
            runtime.partial_output |= bool(holder.get("partial"))
            if kind == "delta":
                if runtime.send:
                    await runtime.send("delta", {"text": data, "model_id": row["model"]["id"]})
            elif kind == "error":
                raise data
            else:
                return data
    finally:
        cancelled.set()
        runtime.partial_output |= bool(holder.get("partial"))
        if stream := holder.get("stream"):
            # EventStream.close only closes the local HTTP body; no inference retry.
            stream.close()
