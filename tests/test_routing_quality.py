import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.gateway_model_decision import select_all
from app.quality_feedback import evidence_for, load_feedback
from app.routing import Task
from app.routing_quality import accepted, quality_policy


def candidate(cid, scores=None, *, price="0.01", benchmark="LiveBench 2", release="2026-11-06"):
    return {
        "candidate_id": cid,
        "model": {"id": cid, "name": cid, "invoke_id": cid, "routing_capabilities": {}},
        "benchmark": {"request_fields": {}, "inference_config": {}},
        "cost_evidence": {"expected_usd": price},
        "measured_evidence": {"suite": "six", "samples": 6, "content_passed": 6,
                              "content_scored": 6, "strict_passed": 6},
        "public_benchmarks": [{
            "id": "evidence-" + cid, "source": "livebench", "benchmark": benchmark,
            "release": release, "metric": "mean_task_score_0_100",
            "configuration": "highest_supported_effort", "status": "completed",
            "scores": scores, "sample_counts": {k: 200 for k in scores},
        }] if scores is not None else [],
    }


def fixture():
    rows = [candidate("cheap", price="0.0001"),
            candidate("strong-a", {"Reasoning": 93, "Data Analysis": 81}),
            candidate("strong-b", {"Reasoning": 91, "Data Analysis": 84}),
            candidate("weak", {"Reasoning": 70, "Data Analysis": 60})]
    profile = {"workflow": "reasoning", "difficulty": "complex", "stronger_model_benefit": "high"}
    task = Task(text="Compare causal explanations from supplied records.",
                routing_preferences={"cost_tier": "high"})
    return task, profile, rows


@pytest.mark.parametrize("tier", ["high", "medium", "low"])
def test_preferences_never_apply_programmatic_quality_cutoffs(tier):
    task, profile, rows = fixture()
    task.routing_preferences.cost_tier = tier
    policy = quality_policy(task, profile, rows)
    assert not policy["active"] and policy["preference_handling"] == "gpu_only"
    assert policy["cost_tier"] == tier
    assert policy["allowed_candidate_ids"] == [r["candidate_id"] for r in rows]
    assert policy["assessments"] == {} and policy["comparison"] is None
    assert "selected_candidate_id" not in policy
    # Both missing public evidence and low scores remain choices for the GPU.
    assert accepted(policy, "cheap") and accepted(policy, "weak")


def test_changed_scores_or_old_configuration_tests_do_not_change_eligibility():
    task, profile, rows = fixture()
    high_effort = copy.deepcopy(rows[1])
    high_effort.update(candidate_id="strong-a-max", measured_evidence={})
    high_effort["benchmark"]["request_fields"] = {"reasoning": {"effort": "max"}}
    rows.append(high_effort)
    policy = quality_policy(task, profile, rows)
    rows[1]["public_benchmarks"][0]["scores"] = {"Reasoning": 0, "Data Analysis": 0}
    high_effort["measured_evidence"] = copy.deepcopy(rows[1]["measured_evidence"])
    assert quality_policy(task, profile, rows) == policy
    assert accepted(policy, "strong-a-max")


def test_missing_or_different_release_scores_never_trigger_a_cpu_fallback():
    task, profile, _ = fixture()
    rows = [candidate("a", {"Reasoning": 99, "Data Analysis": 99}, release="2026-01-01"),
            candidate("b", {"Reasoning": 1, "Data Analysis": 1}, release="2026-02-01"),
            candidate("unmapped")]
    policy = quality_policy(task, profile, rows)
    assert policy["comparison"] is None
    assert set(policy["allowed_candidate_ids"]) == {"a", "b", "unmapped"}
    assert policy["quality_basis"] == "livebench_only"


def test_agent_uses_same_preference_handling_without_quality_cutoff():
    task, profile, rows = fixture()
    for row in rows[1:]:
        row["public_benchmarks"][0]["scores"]["Long Agentic"] = 40
    assert quality_policy(task, profile, rows, agent=True) == quality_policy(task, profile, rows)


@pytest.mark.parametrize("choice", ["cheap", "weak", "unknown"])
def test_one_gpu_selection_for_high_preference_even_with_missing_or_low_scores(choice):
    async def check():
        task, profile, rows = fixture()
        pool = {"candidates": rows, "profile": profile, "reason": "original",
                "selection_source": "gpu_decision_model"}

        class Service:
            def __init__(self):
                self.payloads = []

            async def infer(self, body, request, payload, **kwargs):
                self.payloads.append(payload)
                from tests.gateway_fakes import recorded_aliases
                key = next((k for k, row in recorded_aliases(payload).items()
                            if row["candidate_id"] == choice), "invalid")
                return {"model": "test-gpu", "answers": {"action": {"choice": key}},
                        "timings": {"backend_total_ms": 1}, "usage": None}
        service = Service()
        result = await select_all(service, SimpleNamespace(task=task, provider="liquid"),
                                  SimpleNamespace(state=SimpleNamespace()), pool)
        assert result["selected_candidate_id"] == (None if choice == "unknown" else choice)
        assert result["quality_reselection_count"] == 0 and len(service.payloads) == 1
        payload = service.payloads[0]
        assert len(payload["questions"]["action"]["criteria"]) == 4
        assert "quality-policy=PASS" not in json.dumps(payload)
        assert json.loads(payload["state"])["preference"] == "Prioritize task-relevant capability."
    asyncio.run(check())


def test_reviewed_feedback_is_configuration_endpoint_and_workflow_specific():
    data = load_feedback()
    record = next(r for r in data["records"] if r["case_id"] == "manufacturing-causal-regression")
    row = {"candidate_id": record["candidate_id"], "model": {"invoke_id": record["invoke_id"]}}
    evidence = evidence_for(row, record["workflow"])
    assert evidence and evidence["content_passed"] < evidence["samples"]
    failures = [r for r in evidence["records"] if r["case_id"] == record["case_id"]]
    assert len(failures) == 2 and all(r["content_status"] == "fail" for r in failures)
    assert "复杂产线分析" in evidence["failure_categories"]
    assert evidence_for(row, "coding") is None
    row["model"]["invoke_id"] = "other-endpoint"
    assert evidence_for(row, record["workflow"]) is None


def test_media_quality_also_remains_a_gpu_choice():
    task, profile, rows = fixture()
    profile["workflow"] = "audio_understanding"
    rows[0]["measured_evidence"]["content_passed"] = 0
    rows[0]["measured_evidence"]["strict_passed"] = 0
    policy = quality_policy(task, profile, rows)
    assert policy["quality_basis"] == "modality_evidence"
    assert not policy["active"] and accepted(policy, "cheap")


@pytest.mark.parametrize("stale", [
    "measurement_version", "routing_version", "quality_feedback_version", "public_benchmark_version", None,
])
def test_execution_rechecks_evidence_versions_and_eligibility_before_target(monkeypatch, stale):
    from app import gateway_model_pool, routing
    from app.routing_profile import replay_profile
    from app.routing_selector import SELECTOR_VERSION, load_calibration

    task, profile, rows = fixture()
    versions = dict(measurement_version="measured", routing_version="policy",
                    quality_feedback_version="reviewed", public_benchmark_version="public")
    pool = {"candidates": rows, "profile": replay_profile(task, profile["workflow"]).model_dump(), **versions}
    record = {
        "task": task.model_dump(), "profile": pool["profile"],
        "configuration_id": "cheap" if stale else "not-eligible", "routing_source": "gpu_decision_model",
        "catalog_version": routing.load_catalog()["version"],
        "ranking_version": routing.load_rankings()["version"],
        "calibration_version": load_calibration()["version"],
        "policy_version": SELECTOR_VERSION, **versions,
    }
    if stale:
        record[stale] = "previous-version"
    item = {"username": "tester", "expires_at": routing.unix_time() + 60,
            "record": json.dumps(record)}
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(
            auth=SimpleNamespace(store=SimpleNamespace(get=lambda key: item)))),
        state=SimpleNamespace(user={"username": "tester"}))
    monkeypatch.setattr(gateway_model_pool, "build_pool", lambda *args: pool)

    async def forbidden(*args, **kwargs):
        pytest.fail("Rejected receipt must not invoke Bedrock")
    monkeypatch.setattr(routing, "execute_selected", forbidden)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(routing.auto_execute(routing.AutoExecute(route_token="a" * 32), request))
    assert caught.value.status_code == 409
    assert ("已更新" if stale else "硬约束") in caught.value.detail
