"""Capability/constraint filtering without quality ranking or benchmark-cohort exclusion."""

import copy
import hashlib
import json

from app.bedrock_evidence import load_evidence, measured, measured_scores, public_summary
from app.bedrock_limits import model_limits, output_limit
from app.routing_balance import balanced_pool, preferences
from app.routing_selector import configuration_id

MODEL_ROUTING_VERSION = "gpu-evidence-single-pass-v2"


def build_pool(task, profile, catalog, rankings, exclusion, runtime=None, *, evidence=None):
    from app.routing_media import MEDIA_WORKFLOWS, adapter, eligible, readiness

    evidence = evidence if evidence is not None else load_evidence()
    policy = preferences(task)
    rows, excluded = [], {}
    media = profile.workflow in MEDIA_WORKFLOWS or bool(task.attachments)
    for original in sorted(catalog["models"], key=lambda m: m["id"]):
        model = copy.deepcopy(original)
        mid = model["id"]
        observation = measured(model, evidence)
        caps = observation.get("capabilities", {})
        model["routing_capabilities"] = {**model.get("routing_capabilities", {}), **caps}
        limits = model_limits(model)
        if limits.get("context_tokens"):
            model["routing_capabilities"]["context_tokens"] = limits["context_tokens"]
        effective_cap = output_limit(model, policy.max_output_tokens)
        # Include usable older endpoints unless the caller explicitly excludes legacy.
        effective_task = task
        if "include_legacy" not in task.model_fields_set:
            effective_task = task.model_copy(update={"include_legacy": True})
        reason = eligible(model, effective_task, profile.workflow, exclusion) if media else exclusion(model, effective_task, profile.workflow)
        if media and reason and "专用执行适配器" in reason and caps.get(profile.workflow):
            reason = None
        if not media and profile.workflow != "embedding" and caps.get("converse") is False:
            reason = "该端点的Converse文本调用实测不支持，详见Bedrock评测记录"
        if not media and any(part in mid for part in ("sonic", "pegasus")):
            reason = "该专用端点需要语音或视频输入协议"
        if media and caps.get(profile.workflow) is False:
            reason = "该模态接口实测未成功，详见Bedrock评测记录"
        if runtime and getattr(runtime, "agent_tools", False) and caps.get("tools") is not True:
            reason = "该精确端点尚未通过可跨模型传递的原生工具协议实测"
        if profile.tools == "yes" and not getattr(runtime, "agent_tools", False) and caps.get("tools") is not True:
            reason = "该精确端点缺少已核验的工具协议"
        if profile.structured_output == "yes" and caps.get("structured_output") is not True:
            reason = "该端点未核验提供商强制结构化输出"
        if policy.min_context_tokens is not None:
            size = model["routing_capabilities"].get("context_tokens")
            if not isinstance(size, int) or size < policy.min_context_tokens:
                reason = "上下文上限缺少可核验依据或低于本次要求"
        if limits.get("context_tokens") and len(task.text.encode()) + 64 + effective_cap > limits["context_tokens"]:
            reason = "当前输入和输出预算的保守估算超过已核验上下文上限"
        if reason:
            excluded[mid] = reason
            continue
        sources = []
        mapped = rankings["entries"].get(mid)
        if mapped:
            sources = [mapped, *mapped.get("configurations", [])]
            sources = [b for b in sources if b.get("configuration_status", "verified") == "verified"]
        if policy.benchmark_release != "auto":
            sources = [b for b in sources if b.get("release") == policy.benchmark_release]
            if not sources:
                excluded[mid] = "没有请求指定批次的精确评测配置"
                continue
        configs = {}
        # Consolidate repetitions of the same executable configuration across releases.
        for bench in sources:
            key = json.dumps([bench.get("request_fields", {}), bench.get("inference_config", {})], sort_keys=True)
            configs.setdefault(key, []).append(bench)
        if policy.benchmark_release == "auto":
            configs.setdefault(json.dumps([{}, {}]), [])
        for config, benchmarks in configs.items():
            fields, inference = json.loads(config)
            if (fields.get("thinking", {}).get("budget_tokens", 0) >= effective_cap):
                excluded[mid + "/" + config] = "此配置的思考Token预算不小于本次输出上限"
                continue
            if runtime and getattr(runtime, "agent_tools", False) and fields:
                # Only the tested portable configuration may claim tool-protocol compatibility.
                excluded[mid + "/" + hashlib.sha256(config.encode()).hexdigest()[:8]] = "此推理配置尚未完成可移植工具协议实测"
                continue
            exact = observation if not fields and not inference else {}
            scores = measured_scores(exact, profile.workflow)
            if policy.min_success_rate is not None and (
                not scores or scores.get("success_lower_bound", -1) < policy.min_success_rate
            ):
                excluded[mid + "/" + config] = "同类实测样本不足或成功率置信下界低于要求"
                continue
            if policy.max_latency_ms is not None and (
                not scores or scores.get("p95_ms") is None or scores["p95_ms"] > policy.max_latency_ms
            ):
                excluded[mid + "/" + config] = "同类实测P95未知或超过限制"
                continue
            benchmarks.sort(key=lambda b: b.get("release", ""), reverse=True)
            reference = rankings.get("model_references", {}).get(mid, {})
            metrics = observation.get("pricing") or (benchmarks[0].get("metrics", {}) if benchmarks else reference)
            if media:
                # Text length omits image patches, audio duration and video frames.
                metrics = {}
            bench = {
                "source": "bedrock_development" if exact else "public_evidence" if benchmarks else "unmeasured",
                "benchmark_configuration": "Bedrock " + mid + " / " + config,
                "configuration_status": "verified" if caps.get("converse") or benchmarks or media else "unmeasured",
                "request_fields": fields, "inference_config": inference,
                "metrics": {k: metrics.get(k) for k in ("input_price", "output_price")},
                "pricing_source": observation.get("pricing"),
            }
            cid = configuration_id(mid, bench)
            aid = adapter(model, task, profile.workflow) if media else None
            if media and caps.get(profile.workflow) and profile.workflow in {"vision", "video_understanding"} and not aid:
                aid = "converse_media"
            rows.append({
                "candidate_id": cid, "model": {k: v for k, v in model.items() if k in {
                    "id", "name", "invoke_id", "provider", "input_modalities", "output_modalities",
                    "streaming", "routing_capabilities", "lifecycle", "destination_regions",
                    "account_available", "invocation"}}, "benchmark": bench,
                **({"adapter": aid} if aid else {}),
                "measured_evidence": scores, "measurement_configuration": exact.get("configuration"),
                "specialized_evidence": exact.get("measurements", {}).get("safety"),
                "protocol_evidence": {w: {k: v for k, v in exact.get("measurements", {}).get(w, {}).items()
                                         if k in {"samples", "content_passed", "content_scored", "p95_ms"}}
                                      for w in ("tools", "multi_turn")},
                "measurement_version": evidence["version"],
                "output_token_limit": effective_cap,
                "output_limit_source": limits.get("ceiling_kind"),
                "public_evidence": [public_summary(b, profile.workflow) for b in benchmarks[:1]],
                "price_scope": "AWS on-demand list price" if observation.get("pricing") else "external reference or unknown",
                "task_scores": {}, "business_evidence": None, "p95_ms": scores.get("p95_ms"),
                "selection_cost_usd": None,
                "capability_evidence": {"adapter": aid, "quality_evaluated": bool(scores)},
            })
    pool = balanced_pool(task, profile.workflow, {
        "candidates": rows, "candidate_count": len(rows), "excluded": excluded,
        "catalog_model_count": len(catalog["models"]),
        "available_model_count": len({r["model"]["id"] for r in rows}),
        "catalog_version": catalog["version"], "ranking_version": rankings["version"],
    }, rankings)
    for row in pool["candidates"]:
        row["selection_cost_usd"] = row["cost_evidence"]["expected_usd"]
    if runtime:
        # The ordinary context/health checks remain; the Agent's old model allowlist does not.
        from app.gateway_conversation import Conversation
        runtime.evidence_routing = True
        pool = Conversation.filter_pool(runtime, pool, task)
    missing = readiness(task, profile.workflow) if media else None
    return {
        **pool, "routing_version": MODEL_ROUTING_VERSION, "measurement_version": evidence["version"],
        "profile": profile.model_dump(), "requested_tier": policy.cost_tier,
        "effective_tier": policy.cost_tier, "tier_reason": "成本偏好随评测证据交给决策模型判断。",
        "evidence_mode": "bedrock_measurements_and_public_evidence",
        "selection_source": "gpu_decision_model", "selected_candidate_id": None,
        "frontier": [], "benchmark_group": None, "benchmark_groups": [],
        "cost_mode": "reference_cost", "latency_used": any(r["p95_ms"] is not None for r in pool["candidates"]),
        "execution_ready": missing is None, "execution_notice": missing,
        "reason": missing or "由GPU决策模型读取所有合格候选的评测证据并选择。",
        "filter_summary": {"catalog_models": len(catalog["models"]),
                           "eligible_models": len({r["model"]["id"] for r in pool["candidates"]}),
                           "eligible_configurations": len(pool["candidates"]),
                           "benchmark_cohort_exclusion": False, "quality_top_k_filter": False},
    }
