"""Bedrock-only configuration selection with auditable Pareto tradeoffs."""

import hashlib
import json
import time
from decimal import Decimal
from pathlib import Path

from app.routing_balance import balanced_pool, preferences
from app.routing_calibration import verify_snapshot
from app.routing_evidence import finite, task_metrics
from app.routing_policy import WORKFLOWS

SELECTOR_VERSION = "bedrock-classified-v2"
CALIBRATION_PATH = Path(__file__).with_name("routing_calibration.json")


def load_calibration():
    return verify_snapshot(json.loads(CALIBRATION_PATH.read_text()))


def configuration_id(model_id, benchmark):
    identity = [model_id, benchmark["benchmark_configuration"], benchmark.get("request_fields", {})]
    if benchmark.get("dataset_revision"):
        identity.append(benchmark["dataset_revision"])
    if benchmark.get("inference_config"):
        identity.append(benchmark["inference_config"])
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]


def configurations(catalog, rankings, task, workflow, exclusion):
    """Join by exact Bedrock model ID, including only explicitly measured configurations."""
    rows, rejected, available = [], {}, 0
    for model in sorted(catalog["models"], key=lambda m: m["id"]):
        mid = model["id"]
        reason = exclusion(model, task, workflow)
        if reason:
            rejected[mid] = reason
            continue
        available += 1
        entry = rankings["entries"].get(mid)
        if not entry:
            rejected[mid] = "无精确型号评测映射"
            continue
        # Extra efforts require their own complete evidence; never inherit high-effort scores.
        for bench in [entry, *entry.get("configurations", [])]:
            if bench.get("configuration_status", "verified") != "verified":
                rejected[mid] = bench.get("configuration_note", "评测配置的执行参数待核验")
                continue
            if not all(
                key in bench
                for key in ("source", "benchmark_configuration", "metrics", "request_fields")
            ):
                raise ValueError("Configuration is missing its own evidence or request fields")
            cid = configuration_id(mid, bench)
            if any(row["candidate_id"] == cid for row in rows):
                raise ValueError("Duplicate model configuration")
            rows.append({"candidate_id": cid, "model": model, "benchmark": bench})
    return {
        "candidates": rows,
        "excluded": rejected,
        "candidate_count": len(rows),
        "available_model_count": available,
        "catalog_model_count": len(catalog["models"]),
        "ranking_version": rankings["version"],
        "routing_version": SELECTOR_VERSION,
        "catalog_version": catalog["version"],
    }


def categories(catalog, rankings, task, exclusion):
    result = {}
    for workflow, definition in WORKFLOWS.items():
        pool = configurations(catalog, rankings, task, workflow, exclusion)
        result[workflow] = {
            "label": definition["label"],
            "available_model_count": pool["available_model_count"],
            "available_model_ids": [
                m["id"] for m in catalog["models"] if exclusion(m, task, workflow) is None
            ],
            "configuration_count": len(pool["candidates"]),
            "models": [
                {
                    "id": r["model"]["id"],
                    "configuration_id": r["candidate_id"],
                    "configuration": r["benchmark"]["benchmark_configuration"],
                }
                for r in pool["candidates"]
            ],
            "excluded": pool["excluded"],
        }
    return result


def cohort_key(profile, input_tokens, max_output_tokens):
    # Keep capability, language, difficulty and workload sizes comparable.
    bucket = next(
        (n for n in (1024, 4096, 16384, 65536, 262144, 2_000_000) if input_tokens <= n), None
    )
    return json.dumps(
        [
            profile.workflow,
            profile.difficulty,
            profile.languages,
            profile.tools,
            profile.structured_output,
            bucket,
            max_output_tokens,
        ],
        separators=(",", ":"),
    )


def relevant_scores(benchmark, profile):
    scores = task_metrics(benchmark, profile.workflow)
    if profile.workflow == "embedding":
        required = {"en": ["en"], "zh": ["zh"], "en_zh": ["en", "zh"]}.get(profile.languages, [])
        if not required:
            return {}
        scores = {key: scores.get(key) for key in required}
    elif benchmark.get("source") == "livebench":
        # Older releases do not define Agentic Coding. Retain nulls for
        # incomplete measurements, but do not invent absent metric families.
        scores = {k: v for k, v in scores.items() if k in benchmark.get("category_scores", {})}
    if not scores or any(not finite(v) or not 0 <= v <= 100 for v in scores.values()):
        return {}
    return scores


def effective_tier(profile, requested):
    if requested == "medium":
        if profile.difficulty == "simple" and profile.stronger_model_benefit == "low":
            return "low", "简单任务且更强模型预期收益低：均衡档采用节省策略。"
        if profile.difficulty == "complex" and profile.stronger_model_benefit == "high":
            return "high", "复杂任务且更强模型预期收益高：均衡档优先能力。"
    return requested, "使用请求指定的成本档位。"


def dominates(left, right, axes):
    return all(left[k] <= right[k] for k in axes) and any(left[k] < right[k] for k in axes)


def choose(task, profile, pool, rankings, calibration):
    """No inference here. Unknown evidence never becomes a zero cost or a success rate."""
    pool = balanced_pool(task, profile.workflow, pool, rankings)
    policy = preferences(task)
    context = pool["cost_context"]
    key = cohort_key(profile, context["input_tokens"], context["max_output_tokens"])
    cohort = calibration.get("cohorts", {}).get(key)
    calibrated = bool(
        cohort
        and calibration.get("ranking_version") == rankings["version"]
        and calibration.get("catalog_version") == pool["catalog_version"]
        and calibration.get("expires_at", 0) > time.time()
        and cohort.get("status") == "validated"
    )
    rejected = dict(pool["excluded"])
    rows = []
    for row in pool["candidates"]:
        cid, bench = row["candidate_id"], row["benchmark"]
        caps = row["model"].get("routing_capabilities", {})
        reason = None
        if (
            bench["source"] == "livebench"
            and policy.benchmark_release != "auto"
            and bench.get("release") != policy.benchmark_release
        ):
            reason = f"该请求指定 LiveBench {policy.benchmark_release}，此配置属于其他批次"
        for required, capability in (
            (profile.tools == "yes", "tools"),
            (profile.structured_output == "yes", "structured_output"),
        ):
            if required and caps.get(capability) is not True:
                reason = f"缺少已核实的 {capability} 能力"
        if policy.min_context_tokens is not None:
            limit = caps.get("context_tokens")
            if type(limit) is not int or limit < policy.min_context_tokens:
                reason = "上下文能力未知或不足"
        stats = cohort.get("configurations", {}).get(cid) if calibrated else None
        scores = (
            {"business_success_lower_bound": stats["success_lower_bound"]}
            if stats
            else relevant_scores(bench, profile)
        )
        if calibrated and not stats:
            reason = "没有同一业务验证队列的配置测量"
        if not scores:
            reason = reason or "缺少该任务/语言的可比能力分项"
        if policy.min_success_rate is not None and (
            not stats or stats["success_lower_bound"] < policy.min_success_rate
        ):
            reason = "业务成功率证据不足或低于要求"
        latency = stats.get("p95_ms") if stats else None
        if policy.max_latency_ms is not None and (
            latency is None or latency > policy.max_latency_ms
        ):
            reason = "实测 P95 延迟未知或超过限制"
        if reason:
            rejected[cid] = reason
            continue
        rows.append(
            {
                **row,
                "task_scores": scores,
                "business_evidence": stats,
                "selection_cost_usd": stats["mean_cost_usd"]
                if stats
                else row["cost_evidence"]["expected_usd"],
                "p95_ms": latency,
            }
        )
    benchmark_groups, benchmark_group = [], None
    if rows and not calibrated:
        # Choose a comparable cohort by coverage, then recency, before examining
        # scores. Different datasets must never enter the same Pareto pool.
        groups = {}
        for row in rows:
            bench = row["benchmark"]
            signature = (
                bench["source"],
                bench.get("dataset_revision") or "",
                tuple(sorted(row["task_scores"])),
            )
            groups.setdefault(signature, []).append(row)
        selected_key = max(
            groups,
            key=lambda key: (
                len({r["model"]["id"] for r in groups[key]}),
                max(r["benchmark"].get("release", "") for r in groups[key]),
                key,
            ),
        )
        for signature, members in groups.items():
            group = {
                "source": signature[0],
                "dataset_revision": signature[1],
                "metrics": list(signature[2]),
                "release": members[0]["benchmark"].get("release"),
                "model_count": len({r["model"]["id"] for r in members}),
                "configuration_count": len(members),
                "selected": signature == selected_key,
            }
            benchmark_groups.append(group)
            if signature == selected_key:
                benchmark_group = group
            else:
                for row in members:
                    rejected[row["candidate_id"]] = (
                        "属于其他评测批次或分项，不能与本次选定批次混排；可在更多约束中指定批次"
                    )
        rows = groups[selected_key]
    tier, tier_reason = effective_tier(profile, policy.cost_tier)
    result = {
        **pool,
        "candidates": rows,
        "candidate_count": len(rows),
        "excluded": rejected,
        "profile": profile.model_dump(),
        "requested_tier": policy.cost_tier,
        "effective_tier": tier,
        "tier_reason": tier_reason,
        "evidence_mode": "business_validation" if calibrated else "benchmark_proxy",
        "calibration_version": calibration["version"],
        "business_cost_scope": calibration.get("cost_scope") if calibrated else None,
        "selection_source": "deterministic_policy",
        "selected_candidate_id": None,
        "reason": None,
        "frontier": [],
        "benchmark_group": benchmark_group,
        "benchmark_groups": benchmark_groups,
    }
    if not rows:
        result["reason"] = "该类别没有同时满足限制和证据要求的 Bedrock 配置。"
        return result
    priced = [r for r in rows if r["selection_cost_usd"] is not None]
    if priced:
        for row in rows:
            if row not in priced:
                rejected[row["candidate_id"]] = "费用未知，不参加有报价候选的成本比较"
        rows = priced
    result["cost_mode"] = (
        "measured_cost" if calibrated else "reference_cost" if priced else "unknown_quality_only"
    )
    if not priced:
        tier = "high"
        result["tier_reason"] += " 全部费用未知，仅按能力选择，不能认定最省钱。"
        result["effective_tier"] = tier
    best = {k: max(row["task_scores"][k] for row in rows) for k in rows[0]["task_scores"]}
    for row in rows:
        # Worst per-metric deficit from the attainable best. No cross-category average.
        row["quality_regret"] = max(best[k] - v for k, v in row["task_scores"].items())
        row["cost_axis"] = Decimal(row["selection_cost_usd"]) if priced else Decimal(0)
    axes = ["quality_regret", "cost_axis"]
    if profile.latency == "sensitive" and all(r["p95_ms"] is not None for r in rows):
        axes.append("p95_ms")
    result["latency_used"] = "p95_ms" in axes
    frontier = [r for r in rows if not any(dominates(other, r, axes) for other in rows)]
    spans = {k: (min(r[k] for r in frontier), max(r[k] for r in frontier)) for k in axes}
    for row in frontier:
        row["tradeoff_regret"] = max(
            float((row[k] - low) / (high - low)) if high != low else 0.0
            for k, (low, high) in spans.items()
        )

    def order(row):
        if tier == "low":
            return row["cost_axis"], row["quality_regret"], row["candidate_id"]
        if tier == "high":
            return row["quality_regret"], row["cost_axis"], row["candidate_id"]
        return row["tradeoff_regret"], row["cost_axis"], row["quality_regret"], row["candidate_id"]

    winner = min(frontier, key=order)
    result.update(
        candidates=rows,
        candidate_count=len(rows),
        selected_candidate_id=winner["candidate_id"],
        selected_benchmark=winner["benchmark"],
        frontier=[r["candidate_id"] for r in sorted(frontier, key=order)],
        reason={
            "low": "在非劣候选中选择费用最低的配置。",
            "medium": "在非劣候选中最小化归一化的最大能力、成本偏离。",
            "high": "优先减少任务相关能力的最大短板，再比较费用。",
        }[tier],
    )
    # Internal Decimal axes are not a public price claim.
    for row in rows:
        row.pop("cost_axis", None)
    return result
