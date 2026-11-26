"""Quality evidence and user preference; only the GPU compares model quality."""

from app.public_benchmarks import TEXT_QUALITY_WORKFLOWS
from app.routing_balance import preferences

VERSION = "livebench-preference-v3"


def quality_policy(task, profile, rows, *, agent=False):
    """Keep the audit shape readable without a separate quality eligibility gate."""
    livebench_only = profile["workflow"] in TEXT_QUALITY_WORKFLOWS
    return {
        "version": VERSION,
        "active": False,
        "comparison": None,
        "allowed_candidate_ids": [r["candidate_id"] for r in rows],
        "assessments": {},
        "quality_basis": "livebench_only" if livebench_only else "modality_evidence",
        "cost_tier": preferences(task).cost_tier,
        "preference_handling": "gpu_only",
        "scope": "GPU preference, no programmatic quality exclusion or quality reselection",
        "note": (
            "文本质量仅依据LiveBench，旧开发答题和回归分数不参与选模。"
            if livebench_only else "多模态按相应公开与专项评测判断能力。"
        ) + "能力优先、均衡、成本优先均作为任务偏好交给GPU；"
            "不设置质量分数淘汰门槛，也不因质量偏好增加GPU重选。"
            "缺失成绩保持未知；程序只校验能力、权限和预算等运行约束。",
    }


def attach_quality(selection, task, *, agent=False):
    policy = selection.get("quality_policy") or quality_policy(
        task, selection["profile"], selection["candidates"], agent=agent)
    selection["quality_policy"] = policy
    for row in selection["candidates"]:
        row["quality_assessment"] = None
    return policy


def accepted(policy, candidate_id):
    """Membership in the capability/budget pool, never a benchmark cutoff."""
    return candidate_id is not None and candidate_id in policy["allowed_candidate_ids"]


def explanation(selection, candidate_id):
    if selection.get("quality_policy", {}).get("quality_basis") == "livebench_only":
        return "GPU依据LiveBench相关分项、本次偏好、费用与延迟选择；已核验能力及预算约束。"
    return "GPU依据任务相关评测、本次偏好、费用与延迟选择；已核验能力及预算约束。"
