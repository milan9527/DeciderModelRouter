import copy
import json
from types import SimpleNamespace

import pytest

from app.bedrock_evidence import load_evidence
from app.gateway_joint_decision import joint_payload
from app.gateway_model_pool import build_pool
from app.routing import Task, exclusion, load_catalog, load_rankings
from app.routing_profile import profile_payload, replay_profile
from app.routing_quality import attach_quality


@pytest.mark.parametrize("workflow", ["reasoning", "coding", "general_text", "customer_support"])
@pytest.mark.parametrize("tier", ["high", "medium", "low"])
def test_old_answer_scores_cannot_change_text_pool_policy_or_gpu_input(workflow, tier):
    task = Task(text="Use the supplied information.", routing_preferences={"cost_tier": tier})
    profile = replay_profile(task, workflow)
    evidence = load_evidence()
    changed = copy.deepcopy(evidence)
    for entry in changed["entries"].values():
        for sample in entry.get("measurements", {}).values():
            # Keep observed timing/transport/compatibility unchanged; corrupt answer grades.
            sample.update(content_passed=1234567, content_scored=2345678, samples=3456789,
                          strict_passed=4567890, content_pending=5678901, strict_pending=6789012)
    pools = [build_pool(task, profile, load_catalog(), load_rankings(), exclusion, evidence=data)
             for data in (evidence, changed)]
    payloads = []
    for pool in pools:
        attach_quality(pool, task)
        assert pool["quality_basis"] == "livebench_only"
        assert pool["quality_feedback_version"] is None
        for row in pool["candidates"]:
            assert set(row["measured_evidence"]) <= {"p50_ms", "p95_ms", "call_errors"}
            assert row["quality_feedback"] is None and not row["protocol_evidence"]
            assert all(e["source"] == "livebench" for e in row["public_benchmarks"])
        payload, _ = joint_payload(SimpleNamespace(task=task),
                                   SimpleNamespace(state=SimpleNamespace()), profile_payload(task),
                                   [{**r, "workflow": workflow} for r in pool["candidates"]], assessed=True)
        payloads.append(payload)
    assert pools[0] == pools[1]
    assert payloads[0] == payloads[1]
    cards = json.dumps(payloads[0]["questions"]["action"]["criteria"])
    assert "correctness" not in cards and "strict=" not in cards and "new-regression" not in cards
    assert "1234567" not in cards


def test_latency_constraint_still_uses_exact_endpoint_operational_evidence():
    task = Task(text="分析资料", routing_preferences={"max_latency_ms": 1000})
    pool = build_pool(task, replay_profile(task, "reasoning"), load_catalog(), load_rankings(), exclusion)
    assert pool["candidates"]
    assert all(r["measured_evidence"]["p95_ms"] <= 1000 for r in pool["candidates"])
    assert all(not r["benchmark"]["request_fields"] for r in pool["candidates"])


def test_success_probability_is_not_invented_from_livebench_scores():
    task = Task(text="分析资料", routing_preferences={"min_success_rate": 0.8})
    pool = build_pool(task, replay_profile(task, "reasoning"), load_catalog(), load_rankings(), exclusion)
    assert not pool["candidates"]
    assert any("LiveBench分数不是任务成功概率" in reason for reason in pool["excluded"].values())
