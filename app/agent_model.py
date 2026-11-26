"""Native tool-message boundary for agents using the existing model router."""

import copy
import json

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from app.bedrock_limits import output_limit
from app.gateway_adapter import CACHE_MODELS, cache_usage
from app.gateway_conversation import Conversation, routing_excerpt
from app.industry_scenarios import TOOL_SPECS, system_prompt
from app.routing_balance import preferences

TOOL_MODELS = {
    "amazon.nova-micro-v1:0": "amazon-nova-micro",
    "amazon.nova-lite-v1:0": "amazon-nova-lite",
    "amazon.nova-pro-v1:0": "amazon-nova-pro",
    "anthropic.claude-haiku-4-5-20251001-v1:0": "anthropic-claude-haiku-4-5",
    "anthropic.claude-sonnet-4-5-20250929-v1:0": "anthropic-claude-sonnet-4-5",
    "anthropic.claude-opus-4-1-20250805-v1:0": "anthropic-claude-opus-4-1",
    "anthropic.claude-opus-4-5-20251101-v1:0": "anthropic-claude-opus-4-5",
}
TOOL_EVIDENCE_VERSION = "agent-tools-2026-11-25-v2"


def validate_native_messages(messages):
    """Validate role/block shape, tool pairing and size before any model invocation."""
    if not isinstance(messages, list) or not 1 <= len(messages) <= 80:
        raise ValueError("Agent消息数量须在1–80之间")
    if len(json.dumps(messages, ensure_ascii=False).encode()) > 96_000:
        raise ValueError("Agent上下文超过96KB，请新建会话")
    pending, seen = set(), set()
    names = {s["name"] for s in TOOL_SPECS}
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            raise ValueError("Agent消息必须包含role和content")
        role, blocks = message["role"], message["content"]
        if role != ("user" if index % 2 == 0 else "assistant"):
            raise ValueError("Agent消息必须从user开始交替")
        if not isinstance(blocks, list) or not 1 <= len(blocks) <= 12:
            raise ValueError("Agent内容块数量无效")
        results = set()
        for block in blocks:
            if not isinstance(block, dict) or len(block) != 1:
                raise ValueError("Agent内容块只能有一种类型")
            if "text" in block:
                if not isinstance(block["text"], str) or not block["text"].strip():
                    raise ValueError("Agent文本不能为空")
            elif "toolUse" in block and role == "assistant":
                tool = block["toolUse"]
                if (set(tool) != {"toolUseId", "name", "input"}
                        or not isinstance(tool["toolUseId"], str)
                        or not 1 <= len(tool["toolUseId"]) <= 128
                        or tool["toolUseId"] in seen
                        or tool["name"] not in names or not isinstance(tool["input"], dict)):
                    raise ValueError("Agent工具调用无效")
                pending.add(tool["toolUseId"])
                seen.add(tool["toolUseId"])
            elif "toolResult" in block and role == "user":
                result = block["toolResult"]
                if (set(result) - {"toolUseId", "content", "status"}
                        or result.get("toolUseId") not in pending
                        or result["toolUseId"] in results
                        or result.get("status", "success") not in {"success", "error"}):
                    raise ValueError("Agent工具结果没有对应调用")
                content = result.get("content")
                if not isinstance(content, list) or not content:
                    raise ValueError("工具结果不能为空")
                for item in content:
                    if not isinstance(item, dict) or len(item) != 1 or not set(item) <= {"text", "json"}:
                        raise ValueError("只接受文本或JSON工具结果")
                    if "text" in item and not isinstance(item["text"], str):
                        raise ValueError("工具结果text必须为字符串")
                results.add(result["toolUseId"])
            else:
                raise ValueError("此Agent接口只支持文本和原生工具内容块")
        if role == "user":
            if pending != results:
                raise ValueError("工具调用与结果必须完整配对")
            pending.clear()
    if messages[-1]["role"] != "user" or pending:
        raise ValueError("模型输入必须以用户问题或完整工具结果结束")
    return messages


def readable(message):
    return "\n".join(block.get("text") or json.dumps(block, ensure_ascii=False)
                     for block in message["content"])


class AgentConversation(Conversation):
    def __init__(self, body, history, user, run_id, messages, item):
        super().__init__(body, history, user, run_id)
        self.agent_tools = True
        self.native_messages = copy.deepcopy(validate_native_messages(messages))
        self.agent_system = system_prompt(item)
        self.messages = [{"role": m["role"], "text": readable(m), "attachments": []} for m in messages]
        self.context.update(source="agent_native", history_turns=(len(messages) - 1) // 2,
                            text_bytes=len(json.dumps(messages, ensure_ascii=False).encode())
                            + len(self.agent_system.encode()) + len(json.dumps(TOOL_SPECS).encode()),
                            note="原生user/assistant/toolUse/toolResult完整传递；不把工具结果伪装为用户指令。",
                            tool_evidence_version=TOOL_EVIDENCE_VERSION)

    def task(self, task):
        result = super().task(task)
        policy = preferences(result)
        return result.model_copy(update={"routing_preferences": policy.model_copy(update={
            "input_tokens": max(policy.input_tokens or 0, self.context["text_bytes"] + 64 * len(self.messages)),
        })})

    def classify(self, payload):
        payload = super().classify(payload)
        state = json.loads(payload["state"])
        state["agent_execution"] = {
            "tools": "read-only query_records, read_policy and calculate; no business writes",
            "facts": "Reuse completed tool results; recommendations are not completed actions.",
        }
        if any("toolResult" in block for block in self.native_messages[-1]["content"]):
            state["current_tool_results"] = routing_excerpt(readable(self.native_messages[-1]), 2400)
        return {**payload, "state": json.dumps(state, ensure_ascii=False)}

    def filter_pool(self, pool, task):
        pool = super().filter_pool(pool, task)
        rows, excluded = [], dict(pool["excluded"])
        for row in pool["candidates"]:
            mid = row["model"]["id"]
            if mid not in TOOL_MODELS:
                excluded[row["candidate_id"]] = "尚未核验该精确型号的Agent原生工具协议"
                continue
            # Signed reasoning blocks are provider-specific; do not carry them across models.
            if row["benchmark"].get("request_fields", {}).get("thinking", {}).get("type") in {"enabled", "adaptive"}:
                excluded[row["candidate_id"]] = "此Agent适配器尚未实现跨模型签名推理块"
                continue
            row = copy.deepcopy(row)
            row["model"]["routing_capabilities"] = {
                **row["model"].get("routing_capabilities", {}), "tools": True,
                "tools_source": "https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-"
                + TOOL_MODELS[mid] + ".html",
            }
            rows.append(row)
        # Agent defaults favor fresh comparable evidence, not the largest old
        # family. Require two distinct eligible models so cost remains a choice.
        # Explicit benchmark requests retain their reproducible release.
        if preferences(task).benchmark_release == "auto":
            releases = {}
            for row in rows:
                bench = row["benchmark"]
                if bench["source"] == "livebench" and bench.get("release"):
                    releases.setdefault(bench["release"], set()).add(row["model"]["id"])
            latest = max((release for release, models in releases.items() if len(models) >= 2), default=None)
            if latest:
                current = []
                for row in rows:
                    if row["benchmark"].get("release") == latest:
                        current.append(row)
                    else:
                        excluded[row["candidate_id"]] = (
                            f"Agent默认使用至少两个可用模型的最新可比评测批次 {latest}；"
                            "未跨批次混比分数，可显式指定其他批次"
                        )
                rows = current
        return {**pool, "candidates": rows, "excluded": excluded, "candidate_count": len(rows)}

    def save_success(self, record, result):
        if result.get("complete"):
            record["session_routing"] = {
                "configuration_id": result["configuration_id"], "signature": self.signature,
                "run_id": self.run_id, "model_id": result["model_id"],
            }


async def agent_converse(client, row, task, runtime):
    """Non-streaming native tool result; outer AgentCore stream exposes call/tool progress."""
    messages = copy.deepcopy(runtime.native_messages)
    mid = row["model"]["id"]
    cache = {"enabled": runtime.options.prompt_cache, "supported": mid in CACHE_MODELS,
             "checkpoints": 0, "policy_version": "agent-native-cache-v1",
             "source": "https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html"}
    # Keep checkpoints on complete user/tool-result boundaries as context grows.
    # A short first prompt alone never reaches Claude's cache minimum. Cache
    # points are request metadata and never enter the persisted native transcript.
    if cache["enabled"] and cache["supported"]:
        minimum, maximum = CACHE_MODELS[mid]
        cache.update(minimum_tokens=minimum, ttl_seconds=300)
        positions = {0, len(messages) - 1, len(messages) - 3, len(messages) - 5}
        prefix_bytes = len(runtime.agent_system.encode()) + len(json.dumps(TOOL_SPECS).encode())
        for index, message in enumerate(messages):
            prefix_bytes += len(json.dumps(message, ensure_ascii=False).encode()) + 64
            if index in positions and (maximum is None or prefix_bytes < maximum - 1024):
                message["content"].append({"cachePoint": {"type": "default"}})
                cache["checkpoints"] += 1
    kwargs = {
        "modelId": row["model"]["invoke_id"], "system": [{"text": runtime.agent_system}],
        "messages": messages, "toolConfig": {"tools": [{"toolSpec": s} for s in TOOL_SPECS]},
        "inferenceConfig": {**row["benchmark"].get("inference_config", {}),
                            "maxTokens": output_limit(row["model"], preferences(task).max_output_tokens)},
    }
    if row["benchmark"].get("request_fields"):
        kwargs["additionalModelRequestFields"] = row["benchmark"]["request_fields"]
    response = await run_in_threadpool(client.converse, **kwargs)
    message = copy.deepcopy(response["output"]["message"])
    message.setdefault("role", "assistant")
    # The portable tool tests use answer/tool blocks only. Provider-specific reasoning
    # metadata is not a user-visible answer and cannot be forwarded across model families.
    original_blocks = message["content"]
    omitted = sum(set(b) == {"reasoningContent"} for b in original_blocks)
    empty_text = sum(set(b) == {"text"} and isinstance(b["text"], str) and not b["text"].strip()
                     for b in original_blocks)
    blocks = message["content"] = [
        b for b in original_blocks if set(b) != {"reasoningContent"}
        and not (set(b) == {"text"} and isinstance(b["text"], str) and not b["text"].strip())
    ]
    if any(not set(b) <= {"text", "toolUse"} for b in blocks):
        kinds = sorted({k for b in blocks for k in b if k not in {"text", "toolUse"}})
        raise HTTPException(502, "Agent目标返回未支持的内容块：" + ", ".join(kinds))
    for block in blocks:
        call = block.get("toolUse")
        if call and "type" in call:
            # Bedrock returns this optional discriminator for Claude. Strands'
            # portable toolUse contract contains ID/name/input only.
            if call["type"] != "tool_use":
                raise HTTPException(502, "Agent尚未支持此服务端工具调用类型")
            call.pop("type")
    text = "\n".join(b["text"] for b in blocks if "text" in b)
    tools = [b["toolUse"] for b in blocks if "toolUse" in b]
    probe = ([{"toolResult": {"toolUseId": t.get("toolUseId"), "content": [{"text": "validation"}]}}
              for t in tools] if tools else [{"text": "validation"}])
    stop = response.get("stopReason")
    if blocks or stop != "max_tokens":
        try:
            validate_native_messages([*runtime.native_messages, message, {"role": "user", "content": probe}])
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(502, "Agent目标返回了无效的工具或消息结构") from exc
    complete = (stop == "tool_use" and bool(tools)) or (stop == "end_turn" and bool(text.strip()) and not tools)
    return {"text": text, "message": message, "has_final_text": bool(text.strip()),
            "complete": complete, "turn_complete": stop == "end_turn" and complete,
            "stop_reason": stop, "usage": response.get("usage"), "tool_calls": tools,
            "provider_metadata_omitted": {"reasoningContent": omitted,
                                         **({"empty_text": empty_text} if empty_text else {})},
            "cache": cache_usage(response.get("usage"), cache), "streamed": False, "ttft_ms": None}
