"""Auditable quality constraints; the GPU still chooses the execution model."""

from collections import defaultdict

from app.routing_balance import preferences

VERSION = "quality-priority-v2"
# A policy tolerance, not a confidence interval or a prediction of task accuracy.
MARGIN = 5.0
PUBLIC_FOCUS = {
    "reasoning": ("LiveBench 2", ("Reasoning", "Data Analysis")),
    "coding": ("LiveBench 2", ("Coding",)),
    "general_text": ("LiveBench 1", ("Language", "IF")),
    "customer_support": ("LiveBench 1", ("Language", "IF")),
    "vision": ("MMMU Pro Vision", ("MMMU Pro Vision",)),
}


def dominated(vector, other):
    """Require comparable dimensions, no worse score, and a material advantage."""
    return (all(other[k] >= v for k, v in vector.items())
            and any(other[k] - v >= MARGIN for k, v in vector.items()))


def quality_policy(task, profile, rows, *, agent=False):
    active = preferences(task).cost_tier == "high"
    policy = {
        "version": VERSION, "active": active, "comparison": None,
        "tolerance_points": MARGIN, "allowed_candidate_ids": [r["candidate_id"] for r in rows],
        "assessments": {}, "scope": "quality constraint, not a CPU-selected winner",
        "note": "均衡或成本优先：质量证据供GPU权衡，仍执行能力与预算硬约束。",
    }
    if not active or not rows:
        return policy
    workflow = profile["workflow"]
    benchmark, dimensions = PUBLIC_FOCUS.get(workflow, (None, ()))
    if agent and benchmark == "LiveBench 2":
        dimensions = (*dimensions, "Long Agentic")
    cohorts = defaultdict(dict)
    for row in rows:
        for evidence in row.get("public_benchmarks", []):
            if (evidence["benchmark"] != benchmark or evidence.get("status") != "completed"
                    or not all(k in evidence["scores"] for k in dimensions)):
                continue
            key = (evidence["source"], evidence["benchmark"], evidence["release"], evidence["metric"])
            cohorts[key][row["candidate_id"]] = {
                "scores": {k: evidence["scores"][k] for k in dimensions},
                "evidence_id": evidence["id"], "model_id": row["model"]["id"],
                "configuration": evidence["configuration"],
            }
    # Compare one disclosed batch. Never average different suites, infer a missing
    # score, or compare a model against duplicate configurations of itself.
    comparable = [(k, v) for k, v in cohorts.items()
                  if len({r["model_id"] for r in v.values()}) >= 2]
    if comparable:
        key, observations = max(comparable, key=lambda pair: pair[0][2])
        policy["comparison"] = {
            "source": key[0], "benchmark": key[1], "release": key[2], "metric": key[3],
            "dimensions": list(dimensions),
            "scope": "external_same_model_not_current_Bedrock_configuration",
        }
        policy["note"] = (
            "能力优先：先核验同批次相关分项；不选择被明确支配或缺少该可比依据的配置。"
            "5分为路由容差，非统计置信界；外部高推理档不保证本次默认档效果。"
            "同型号已有直接实测配置时，未测推理档不能借用质量与时延成绩。"
            "费用与沿用模型只在通过质量约束后由GPU权衡。"
        )
    else:
        # Native media and newer models may have no comparable public suite.
        # Compare only direct tests from the same suite; keep the small-sample
        # limitation explicit instead of inventing public capability scores.
        direct = defaultdict(dict)
        for row in rows:
            evidence = row.get("measured_evidence") or {}
            if (evidence.get("suite") and evidence.get("samples")
                    and evidence.get("content_scored") == evidence["samples"]
                    and not evidence.get("call_errors") and not evidence.get("content_pending")
                    and evidence.get("strict_passed") is not None):
                direct[evidence["suite"]][row["candidate_id"]] = {
                    "scores": {
                        "Content": 100 * evidence["content_passed"] / evidence["samples"],
                        "Complete output": 100 * evidence["strict_passed"] / evidence["samples"],
                    },
                    "evidence_id": evidence["suite"], "model_id": row["model"]["id"],
                    "configuration": "exact_measured_endpoint_configuration",
                }
        available = [(k, v) for k, v in direct.items()
                     if len({r["model_id"] for r in v.values()}) >= 2]
        if not available:
            policy["note"] = "缺少可比较的同批次质量证据；由GPU说明范围内选择，不能保证能力最佳。"
            policy["evidence_gap"] = True
            return policy
        suite, observations = max(available, key=lambda pair: len(pair[1]))
        policy["comparison"] = {"source": "bedrock_development", "suite": suite,
                                "dimensions": ["Content", "Complete output"],
                                "scope": "small_visible_development_sample"}
        policy["note"] = (
            "无可比公开分项，使用相同开发套件核验内容与完整输出；小样本不能证明能力相当或最佳。"
            "新增任务回归另列，不能与原套件直接混成成功概率。"
        )
    allowed = []
    measured_models = {
        row["model"]["id"] for row in rows
        if (row.get("measured_evidence") or {}).get("content_scored", 0) > 0
        and (row.get("measured_evidence") or {}).get("p95_ms") is not None
        and not (row.get("measured_evidence") or {}).get("call_errors")
    }
    for row in rows:
        cid = row["candidate_id"]
        observation = observations.get(cid)
        direct = row.get("measured_evidence") or {}
        unmeasured_configuration = (
            row["model"]["id"] in measured_models
            and (not direct.get("content_scored") or direct.get("p95_ms") is None)
        )
        witnesses = []
        if observation:
            witnesses = [
                {"candidate_id": other_id, "model_id": other["model_id"],
                 "evidence_id": other["evidence_id"], "scores": other["scores"]}
                for other_id, other in observations.items()
                if other["model_id"] != observation["model_id"]
                and dominated(observation["scores"], other["scores"])
            ]
        accepted = observation is not None and not witnesses and not unmeasured_configuration
        reason = ("同批次相关分项通过质量约束，最终仍由GPU选择。" if accepted else
                  "同型号已有直接实测的可用配置；此推理档缺少精确端点的质量和时延实测，不继承默认档成绩。"
                  if unmeasured_configuration else
                  "同批次相关分项存在明确更强的候选；能力优先不接受本配置。" if witnesses else
                  "缺少本次可比质量依据；不补零，但能力优先不能据此认定与已验证候选相当。")
        policy["assessments"][cid] = {
            "accepted": accepted, "reason": reason,
            "evidence": observation, "stronger_references": witnesses,
            "unmeasured_configuration": unmeasured_configuration,
        }
        if accepted:
            allowed.append(cid)
    policy["allowed_candidate_ids"] = allowed
    return policy


def attach_quality(selection, task, *, agent=False):
    # Execution fallback keeps the original quality standard. A target outage
    # must not make an originally rejected model become the new quality leader.
    policy = selection.get("quality_policy") or quality_policy(
        task, selection["profile"], selection["candidates"], agent=agent)
    selection["quality_policy"] = policy
    for row in selection["candidates"]:
        row["quality_assessment"] = policy["assessments"].get(row["candidate_id"])
    return policy


def accepted(policy, candidate_id):
    return candidate_id is not None and candidate_id in policy["allowed_candidate_ids"]


def explanation(selection, candidate_id):
    policy = selection.get("quality_policy", {})
    if not policy.get("active"):
        return "GPU根据任务质量、费用与延迟选择；已核验能力及预算约束。"
    row = policy.get("assessments", {}).get(candidate_id)
    if row and row["accepted"]:
        reference = policy["comparison"]
        source = reference.get("benchmark", "Bedrock同套件开发测试")
        scores = "、".join(f"{k} {v:g}" for k, v in row["evidence"]["scores"].items())
        return f"能力优先校验通过：{source}，{scores}；最终型号由GPU选择。外部成绩不保证本次答案正确。"
    return policy.get("note", "质量依据不足。")
